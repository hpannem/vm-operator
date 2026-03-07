#!/usr/bin/env python3
# Copyright (c) 2026 Broadcom. All Rights Reserved.
# Broadcom Confidential. The term "Broadcom" refers to Broadcom Inc.
# and/or its subsidiaries.

# ABOUTME: Script to test OVF deployment via VM Service on Supervisor.
# ABOUTME: Uploads OVF to Content Library via pyvmomi, deploys VM via kubectl.

"""
ovf_deploy_test.py - Test OVF deployment via VM Service

This script:
1. Connects to vCenter using pyvmomi (API)
2. SSHs to vCenter as root to retrieve Supervisor credentials via decryptK8Pwd.py
3. Uploads an OVF from a URL to a Content Library
4. SSHs to Supervisor and deploys a VM using VM Service
5. Waits for VM to get an IP and verifies reachability

Usage:
    python ovf_deploy_test.py \
        --vcenter <vcenter-ip> \
        --vcenter-password <vc-api-password> \
        --vcenter-root-password <vc-root-ssh-password>

    # If vCenter API password and root SSH password are the same:
    python ovf_deploy_test.py \
        --vcenter <vcenter-ip> \
        --vcenter-password <password>

Requirements:
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import ssl
import sys
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import paramiko
import requests
import yaml
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim

# Disable SSL warnings for self-signed certificates
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Default values
DEFAULT_OVF_BASE_URL = "https://packages.vcfd.broadcom.net/ui/native/cls-generic-virtual/testdata/"
DEFAULT_OVF_URL = "https://packages.vcfd.broadcom.net/artifactory/cls-generic-virtual/testdata/BIGIP-16.0.1.1-0.0.6.ALL-vmware.ova"
DEFAULT_NAMESPACE = "ovftest"
DEFAULT_CONTENT_LIBRARY = "ovftest"
DEFAULT_VM_CLASS = "best-effort-xsmall"
DEFAULT_VCENTER_USER = "administrator@vsphere.local"
STORAGE_CLASS = "wcpglobal-storage-profile"


# Timeouts
VMI_WAIT_TIMEOUT = 300  # 5 minutes
VM_TOOLS_WAIT_TIMEOUT = 300  # 5 minutes
POLL_INTERVAL = 10  # seconds

class UntrustedSourceError(RuntimeError):
    """Raised when vCenter cannot trust the source server's TLS certificate."""


class SetupError(RuntimeError):
    """
    Raised when the test harness fails before the VM is deployed —
    e.g. content library upload error, VMI never appeared, kubectl failure.
    These are infrastructure/script problems, not OVF compatibility issues.
    """


# OVF XML namespaces
OVF_NS = "http://schemas.dmtf.org/ovf/envelope/1"
VMW_NS = "http://www.vmware.com/schema/ovf"
RASD_NS = "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"


@dataclass
class OvfProperty:
    """Represents a single OVF property from ProductSection."""
    key: str
    type: str
    default: str
    user_configurable: bool
    label: str
    description: str


@dataclass
class OvfNetwork:
    """Represents a logical network defined in the OVF NetworkSection."""
    name: str
    description: str


@dataclass
class OvfInfo:
    """Parsed information from an OVF envelope."""
    name: str
    networks: list[OvfNetwork] = field(default_factory=list)
    properties: list[OvfProperty] = field(default_factory=list)
    is_vapp: bool = False
    guest_id: str = ""  # OVF OperatingSystemSection osType, empty if not specified

    def has_properties(self) -> bool:
        return len(self.properties) > 0

    def has_networks(self) -> bool:
        return len(self.networks) > 0


def parse_ovf(ovf_content: str) -> OvfInfo:
    """
    Parse an OVF XML string and extract networks and vApp properties.

    Args:
        ovf_content: OVF XML as a string

    Returns:
        OvfInfo with parsed networks and properties
    """
    root = ET.fromstring(ovf_content)

    # A vApp envelope has VirtualSystemCollection as the top-level content element.
    is_vapp = root.find(f"{{{OVF_NS}}}VirtualSystemCollection") is not None

    # Get VM name
    name_el = root.find(f".//{{{OVF_NS}}}VirtualSystem/{{{OVF_NS}}}Name")
    name = name_el.text if name_el is not None else "unknown"

    info = OvfInfo(name=name, is_vapp=is_vapp)

    # Parse OperatingSystemSection for guestId
    os_section = root.find(f".//{{{OVF_NS}}}OperatingSystemSection")
    if os_section is not None:
        # osType attribute holds the VMware guest OS identifier (e.g. "vmwarePhoton64Guest")
        info.guest_id = os_section.get(f"{{{VMW_NS}}}osType", "")

    # Parse NetworkSection
    for net in root.findall(f".//{{{OVF_NS}}}NetworkSection/{{{OVF_NS}}}Network"):
        net_name = net.get(f"{{{OVF_NS}}}name", "")
        desc_el = net.find(f"{{{OVF_NS}}}Description")
        desc = desc_el.text if desc_el is not None else ""
        info.networks.append(OvfNetwork(name=net_name, description=desc))

    # Parse ProductSection properties
    for prop in root.findall(f".//{{{OVF_NS}}}ProductSection/{{{OVF_NS}}}Property"):
        key = prop.get(f"{{{OVF_NS}}}key", "")
        typ = prop.get(f"{{{OVF_NS}}}type", "string")
        default = prop.get(f"{{{OVF_NS}}}value", "")
        user_configurable = prop.get(f"{{{OVF_NS}}}userConfigurable", "false").lower() == "true"
        label_el = prop.find(f"{{{OVF_NS}}}Label")
        label = label_el.text if label_el is not None else key
        desc_el = prop.find(f"{{{OVF_NS}}}Description")
        desc = desc_el.text if desc_el is not None else ""
        info.properties.append(OvfProperty(
            key=key, type=typ, default=default,
            user_configurable=user_configurable,
            label=label, description=desc
        ))

    return info


def fetch_ovf_from_url(ovf_url: str) -> Optional[OvfInfo]:
    """
    Fetch and parse OVF info from a URL.

    Handles both .ovf files (fetched directly) and .ova files
    (fetched partially - only the OVF member is needed).
    """
    parsed = urlparse(ovf_url)
    filename = os.path.basename(parsed.path)

    try:
        if filename.endswith('.ovf'):
            response = requests.get(ovf_url, verify=False, timeout=30)
            response.raise_for_status()
            return parse_ovf(response.text)

        elif filename.endswith('.ova'):
            # OVA is a tar archive. Stream it, accumulating data until we can
            # successfully extract the .ovf member (typically the first entry).
            # We read up to 10MB which is more than enough for any OVF descriptor.
            response = requests.get(ovf_url, verify=False, stream=True, timeout=60)
            response.raise_for_status()

            buf = io.BytesIO()
            for chunk in response.iter_content(chunk_size=65536):
                buf.write(chunk)
                if buf.tell() >= 10 * 1024 * 1024:
                    break
            response.close()

            buf.seek(0)
            try:
                # mode='r|' = streaming tar; tolerates partial data at EOF
                with tarfile.open(fileobj=buf, mode='r|') as tar:
                    for member in tar:
                        if member.name.endswith('.ovf'):
                            f = tar.extractfile(member)
                            if f:
                                return parse_ovf(f.read().decode())
            except tarfile.TarError as e:
                print(f"  Warning: Could not read OVA tar: {e}")

    except Exception as e:
        print(f"  Warning: Could not fetch OVF from {ovf_url}: {e}")

    return None


def fetch_ovf_from_file(ova_path: str) -> Optional[OvfInfo]:
    """Parse OVF info from a local OVA or OVF file."""
    try:
        if ova_path.endswith('.ovf'):
            with open(ova_path) as f:
                return parse_ovf(f.read())
        elif ova_path.endswith('.ova'):
            with tarfile.open(ova_path) as tar:
                for member in tar.getmembers():
                    if member.name.endswith('.ovf'):
                        f = tar.extractfile(member)
                        if f:
                            return parse_ovf(f.read().decode())
    except Exception as e:
        print(f"  Warning: Could not parse OVF from {ova_path}: {e}")
    return None


class VCenterClient:
    """Client for vCenter operations using pyvmomi."""

    def __init__(self, host: str, user: str, password: str, root_password: Optional[str] = None):
        self.host = host
        self.user = user
        self.password = password
        self.root_password = root_password or password
        self.si = None
        self.rest_session = None
        self.ssh = None

    def connect(self) -> None:
        """Connect to vCenter."""
        print(f"Connecting to vCenter {self.host}...")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        self.si = SmartConnect(
            host=self.host,
            user=self.user,
            pwd=self.password,
            sslContext=context
        )
        print("  Connected to vCenter via SOAP API")

        # Create REST session for Content Library operations
        self._create_rest_session()

        # Create SSH connection for Supervisor credential retrieval
        self._create_ssh_session()

    def _create_rest_session(self) -> None:
        """Create REST API session for Content Library operations."""
        self.rest_session = requests.Session()
        self.rest_session.verify = False

        # Authenticate
        auth_url = f"https://{self.host}/api/session"
        response = self.rest_session.post(
            auth_url,
            auth=(self.user, self.password)
        )
        response.raise_for_status()
        session_id = response.json()
        self.rest_session.headers.update({
            "vmware-api-session-id": session_id
        })
        print("  Connected to vCenter REST API")

    def _create_ssh_session(self) -> None:
        """Create SSH session to vCenter for Supervisor credential retrieval."""
        print(f"  Connecting to vCenter via SSH...")
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(
            hostname=self.host,
            username="root",
            password=self.root_password,
            look_for_keys=False,
            allow_agent=False
        )
        print("  Connected to vCenter via SSH")

    def disconnect(self) -> None:
        """Disconnect from vCenter."""
        if self.ssh:
            self.ssh.close()
        if self.si:
            Disconnect(self.si)
            print("Disconnected from vCenter")

    def is_vm_powered_on(self, vm_name: str) -> bool:
        """
        Check whether a VM is powered on via the vSphere API.
        Searches all VMs in the inventory by name.
        Returns True if found and in poweredOn state.
        """
        content = self.si.RetrieveContent()
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        try:
            for vm in container.view:
                if vm.name == vm_name:
                    return vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOn
        finally:
            container.Destroy()
        return False

    def get_default_deploy_target(self,
                                   datacenter: Optional[str] = None,
                                   cluster: Optional[str] = None,
                                   datastore: Optional[str] = None) -> dict:
        """
        Return a deploy target dict with resource_pool_id, folder_id, and datastore_id.

        If datacenter/cluster/datastore names are not provided, auto-picks the first
        available ones from the vCenter inventory via REST API.
        """
        # List datacenters
        dc_url = f"https://{self.host}/api/vcenter/datacenter"
        dcs = self.rest_session.get(dc_url).json()
        if not dcs:
            raise RuntimeError("No datacenters found in vCenter")
        if datacenter:
            dc = next((d for d in dcs if d.get("name") == datacenter), None)
            if not dc:
                raise RuntimeError(f"Datacenter '{datacenter}' not found")
        else:
            dc = dcs[0]
        dc_id = dc["datacenter"]
        print(f"  Using datacenter: {dc['name']} ({dc_id})")

        # List clusters in datacenter
        cl_url = f"https://{self.host}/api/vcenter/cluster"
        cl_params = {"filter.datacenters": dc_id}
        clusters = self.rest_session.get(cl_url, params=cl_params).json()
        if not clusters:
            raise RuntimeError(f"No clusters found in datacenter '{dc['name']}'")
        if cluster:
            cl = next((c for c in clusters if c.get("name") == cluster), None)
            if not cl:
                raise RuntimeError(f"Cluster '{cluster}' not found")
        else:
            cl = clusters[0]
        cl_id = cl["cluster"]
        print(f"  Using cluster: {cl['name']} ({cl_id})")

        # Get the cluster's root resource pool
        cl_detail = self.rest_session.get(
            f"https://{self.host}/api/vcenter/cluster/{cl_id}"
        ).json()
        rp_id = cl_detail.get("resource_pool")
        if not rp_id:
            raise RuntimeError(f"Could not find resource pool for cluster '{cl['name']}'")
        print(f"  Using resource pool: {rp_id}")

        # Get the datacenter's default VM folder
        dc_detail = self.rest_session.get(
            f"https://{self.host}/api/vcenter/datacenter/{dc_id}"
        ).json()
        folder_id = dc_detail.get("vm_folder")
        if not folder_id:
            raise RuntimeError(f"Could not find VM folder for datacenter '{dc['name']}'")
        print(f"  Using VM folder: {folder_id}")

        # List datastores accessible from the cluster
        ds_url = f"https://{self.host}/api/vcenter/datastore"
        ds_params = {"filter.datacenters": dc_id}
        datastores = self.rest_session.get(ds_url, params=ds_params).json()
        if not datastores:
            raise RuntimeError(f"No datastores found in datacenter '{dc['name']}'")
        if datastore:
            ds = next((d for d in datastores if d.get("name") == datastore), None)
            if not ds:
                raise RuntimeError(f"Datastore '{datastore}' not found")
        else:
            # Prefer a writable, non-local datastore
            writable = [d for d in datastores
                        if d.get("accessible") and d.get("type") not in ("VSAN",)]
            ds = writable[0] if writable else datastores[0]
        ds_id = ds["datastore"]
        print(f"  Using datastore: {ds['name']} ({ds_id})")

        return {
            "resource_pool_id": rp_id,
            "folder_id": folder_id,
            "datastore_id": ds_id,
        }

    def deploy_library_item(self, item_id: str, vm_name: str,
                             resource_pool_id: str, folder_id: str,
                             datastore_id: str) -> str:
        """
        Deploy a content library item as a VM using the vSphere REST API.
        Returns the VM ID of the deployed VM.
        """
        url = f"https://{self.host}/api/vcenter/ovf/library-item/{item_id}?action=deploy"
        spec = {
            "deployment_spec": {
                "name": vm_name,
                "accept_all_EULA": True,
                "storage_provisioning": "thin",
                "default_datastore_id": datastore_id,
            },
            "target": {
                "resource_pool_id": resource_pool_id,
                "folder_id": folder_id,
            }
        }
        response = self.rest_session.post(url, json=spec)
        if not response.ok:
            raise RuntimeError(
                f"Failed to deploy library item: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        result = response.json()
        # Response contains succeeded flag and resource_id with vm id
        if not result.get("succeeded"):
            errors = result.get("errors", [])
            raise RuntimeError(f"OVF deploy failed: {errors}")
        vm_id = result.get("resource_id", {}).get("id")
        if not vm_id:
            raise RuntimeError(f"Deploy succeeded but no VM ID returned: {result}")
        print(f"  Deployed VM '{vm_name}' with ID: {vm_id}")
        return vm_id

    def power_on_vm_by_id(self, vm_id: str) -> None:
        """Power on a VM by its vSphere VM ID."""
        url = f"https://{self.host}/api/vcenter/vm/{vm_id}/power?action=start"
        response = self.rest_session.post(url)
        if not response.ok and response.status_code != 400:
            # 400 may mean already powered on
            raise RuntimeError(
                f"Failed to power on VM {vm_id}: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )

    def wait_for_vm_powered_on_by_id(self, vm_id: str,
                                      timeout: int = VM_TOOLS_WAIT_TIMEOUT) -> bool:
        """
        Poll VM power state by VM ID via REST API until powered on or timeout.
        Returns True if powered on within timeout.
        """
        url = f"https://{self.host}/api/vcenter/vm/{vm_id}/power"
        start = time.time()
        while time.time() - start < timeout:
            r = self.rest_session.get(url)
            if r.ok and r.json().get("state") == "POWERED_ON":
                return True
            time.sleep(POLL_INTERVAL)
        return False

    def delete_vm_by_id(self, vm_id: str) -> None:
        """Power off and delete a VM by its vSphere VM ID (best-effort)."""
        try:
            self.rest_session.post(
                f"https://{self.host}/api/vcenter/vm/{vm_id}/power?action=stop"
            )
        except Exception:
            pass
        try:
            r = self.rest_session.delete(f"https://{self.host}/api/vcenter/vm/{vm_id}")
            if r.ok:
                print(f"  Deleted VM {vm_id}")
        except Exception as e:
            print(f"  Warning: could not delete VM {vm_id}: {e}")

    def get_supervisor_credentials(self) -> tuple[str, str]:
        """
        Get Supervisor IP and password from vCenter.

        Uses /usr/lib/vmware-wcp/decryptK8Pwd.py which outputs both
        the Supervisor IP and the root password for SSH access.

        Returns:
            Tuple of (supervisor_ip, supervisor_password)
        """
        print("Retrieving Supervisor credentials from vCenter...")

        # Use decryptK8Pwd.py which provides Supervisor (K8s control plane) credentials
        stdin, stdout, stderr = self.ssh.exec_command(
            "/usr/lib/vmware-wcp/decryptK8Pwd.py"
        )
        output = stdout.read().decode()
        exit_code = stdout.channel.recv_exit_status()

        if exit_code == 0 and output:
            # Parse the output - format is typically:
            # IP: x.x.x.x
            # PWD: password
            lines = output.strip().split('\n')
            sv_ip = None
            sv_pwd = None

            for line in lines:
                line = line.strip()
                if line.startswith('IP:'):
                    sv_ip = line.split(':', 1)[1].strip()
                elif line.startswith('PWD:'):
                    sv_pwd = line.split(':', 1)[1].strip()

            if sv_ip and sv_pwd:
                print(f"  Found Supervisor IP: {sv_ip}")
                print(f"  Found Supervisor password: {'*' * len(sv_pwd)}")
                return sv_ip, sv_pwd

        # If decryptK8Pwd.py didn't work, try to get IP from API and use provided password
        sv_ip = self._get_supervisor_ip_from_api()
        if sv_ip:
            print(f"  Found Supervisor IP from API: {sv_ip}")
            print("  WARNING: Could not retrieve Supervisor password automatically.")
            print("           Use --supervisor-root-password to provide it.")
            raise RuntimeError(
                "Could not retrieve Supervisor password. "
                "Please provide --supervisor-root-password"
            )

        raise RuntimeError(
            "Could not retrieve Supervisor credentials from vCenter. "
            "Make sure WCP is enabled and /usr/lib/vmware-wcp/decryptK8Pwd.py is available."
        )

    def _get_supervisor_ip_from_api(self) -> Optional[str]:
        """Get Supervisor control plane IP from vSphere API."""
        # Try to get from WCP cluster configuration
        stdin, stdout, stderr = self.ssh.exec_command(
            "grep -r 'control-plane' /etc/vmware-wcp/ 2>/dev/null | head -1 || "
            "cat /etc/vmware-wcp/wcpsvc.yaml 2>/dev/null | grep -i 'address' | head -1"
        )
        output = stdout.read().decode().strip()

        # Try to extract IP from output
        ip_pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
        match = ip_pattern.search(output)
        if match:
            return match.group(1)

        # Try REST API to get namespaces/clusters info
        try:
            url = f"https://{self.host}/api/vcenter/namespace-management/clusters"
            response = self.rest_session.get(url)
            if response.status_code == 200:
                clusters = response.json()
                for cluster in clusters:
                    cluster_id = cluster.get("cluster")
                    if cluster_id:
                        detail_url = f"https://{self.host}/api/vcenter/namespace-management/clusters/{cluster_id}"
                        detail_response = self.rest_session.get(detail_url)
                        if detail_response.status_code == 200:
                            detail = detail_response.json()
                            api_server = detail.get("api_server_cluster_endpoint")
                            if api_server:
                                return api_server
        except Exception as e:
            print(f"  Warning: Could not get Supervisor IP from API: {e}")

        return None

    def find_content_library(self, name: str) -> Optional[str]:
        """Find a content library by name and return its ID."""
        url = f"https://{self.host}/api/content/library"
        response = self.rest_session.get(url)
        response.raise_for_status()

        library_ids = response.json()
        for lib_id in library_ids:
            lib_url = f"https://{self.host}/api/content/library/{lib_id}"
            lib_response = self.rest_session.get(lib_url)
            lib_response.raise_for_status()
            lib_info = lib_response.json()
            if lib_info.get("name") == name:
                print(f"  Found content library '{name}' with ID: {lib_id}")
                return lib_id

        return None

    def find_library_item(self, library_id: str, item_name: str) -> Optional[dict]:
        """
        Find a library item by name in a library.
        Returns a dict with 'id' and 'size' keys, or None if not found.
        """
        url = f"https://{self.host}/api/content/library/item"
        params = {"library_id": library_id}
        response = self.rest_session.get(url, params=params)
        response.raise_for_status()

        item_ids = response.json()
        for item_id in item_ids:
            item_url = f"https://{self.host}/api/content/library/item/{item_id}"
            item_response = self.rest_session.get(item_url)
            item_response.raise_for_status()
            item_info = item_response.json()
            if item_info.get("name") == item_name:
                return {"id": item_id, "size": item_info.get("size", 0)}

        return None

    def delete_library_item(self, item_id: str) -> None:
        """Delete a content library item by ID."""
        url = f"https://{self.host}/api/content/library/item/{item_id}"
        response = self.rest_session.delete(url)
        if not response.ok:
            raise RuntimeError(
                f"Failed to delete library item {item_id}: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        print(f"  Deleted incomplete library item {item_id}")

    def upload_ovf(self, library_id: str, source: str, item_name: str) -> str:
        """
        Upload an OVF/OVA to the content library from a URL or local file path.
        If an item with the same name exists but has 0 bytes (failed prior upload),
        it is deleted and re-uploaded.
        """
        print(f"Uploading OVF '{item_name}' from {source} to content library...")

        # Check if item already exists
        existing_item = self.find_library_item(library_id, item_name)
        if existing_item:
            if existing_item["size"] > 0:
                print(f"  Item '{item_name}' already exists in library ({existing_item['size']} bytes), skipping upload")
                return existing_item["id"]
            else:
                print(f"  Item '{item_name}' exists but has 0 bytes (incomplete upload), deleting and re-uploading...")
                self.delete_library_item(existing_item["id"])

        ovf_url = source

        # Create library item
        create_spec = {
            "name": item_name,
            "library_id": library_id,
            "type": "ovf"
        }

        create_url = f"https://{self.host}/api/content/library/item"
        response = self.rest_session.post(create_url, json=create_spec)
        if not response.ok:
            raise RuntimeError(
                f"Failed to create library item: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        item_id = response.json()
        print(f"  Created library item with ID: {item_id}")

        # Create update session
        session_spec = {"library_item_id": item_id}
        session_url = f"https://{self.host}/api/content/library/item/update-session"
        response = self.rest_session.post(session_url, json=session_spec)
        if not response.ok:
            raise RuntimeError(
                f"Failed to create update session: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        session_id = response.json()
        print(f"  Created update session: {session_id}")

        try:
            # Download OVF/OVA and upload files
            self._upload_ovf_files(session_id, source)

            # For PULL transfers, vCenter downloads files asynchronously.
            # Wait until every file in the session reaches READY before completing.
            self._wait_for_session_files_ready(session_id)

            # Complete the session
            complete_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}?action=complete"
            response = self.rest_session.post(complete_url)
            if not response.ok:
                body = response.text
                if "certificate" in body.lower() and (
                    "expired" in body.lower() or "not trusted" in body.lower()
                    or "certificate_unknown" in body.lower()
                ):
                    raise UntrustedSourceError(
                        f"Source server TLS certificate not trusted by vCenter: {body}"
                    )
                raise RuntimeError(
                    f"Failed to complete update session: "
                    f"{response.status_code} {response.reason}\n{body}"
                )
            print("  Upload completed successfully")

        except UntrustedSourceError:
            # Cancel the session and clean up — source cert is expired/untrusted,
            # vCenter won't accept the content regardless of what transferred.
            cancel_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}?action=cancel"
            self.rest_session.post(cancel_url)
            try:
                self.delete_library_item(item_id)
            except Exception:
                pass
            raise

        except Exception as e:
            # Cancel the session and clean up the incomplete library item
            cancel_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}?action=cancel"
            self.rest_session.post(cancel_url)
            try:
                self.delete_library_item(item_id)
            except Exception:
                pass
            raise e

        return item_id

    def _wait_for_session_files_ready(self, session_id: str,
                                      timeout: int = 3600, poll_interval: int = 10) -> int:
        """
        Poll the update session file list until every file is READY.
        Returns the total bytes of all transferred files.
        Raises RuntimeError if any file ends up in ERROR state or the timeout expires.
        This is required for PULL transfers where vCenter downloads files asynchronously.
        """
        files_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}/file"
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self.rest_session.get(files_url)
            response.raise_for_status()
            files = response.json()

            statuses = {f["name"]: f.get("status", "UNKNOWN") for f in files}
            not_ready = {name: st for name, st in statuses.items() if st != "READY"}

            if not not_ready:
                total_bytes = sum(f.get("size", 0) for f in files)
                print(f"  All {len(files)} file(s) ready ({total_bytes} bytes)")
                return total_bytes

            # If every non-ready file is in ERROR, fail fast with all errors at once.
            errors = {name: st for name, st in not_ready.items() if st == "ERROR"}
            if errors and errors.keys() == not_ready.keys():
                # Fetch per-file error details if available
                details = {}
                for f in files:
                    if f.get("status") == "ERROR":
                        details[f["name"]] = f.get("error_message") or f.get("status")
                details_str = str(details)
                if "certificate" in details_str.lower() and (
                    "expired" in details_str.lower() or "not trusted" in details_str.lower()
                    or "certificate_unknown" in details_str.lower()
                ):
                    raise UntrustedSourceError(
                        f"Source server TLS certificate not trusted by vCenter: {details_str}"
                    )
                raise RuntimeError(
                    f"File transfer failed for: {list(errors.keys())}. "
                    f"Details: {details}"
                )

            transferring = list(not_ready.keys())
            print(f"  Waiting for transfer: {transferring} ...")
            time.sleep(poll_interval)

        raise RuntimeError(
            f"Timed out after {timeout}s waiting for session {session_id} files to become READY"
        )

    @staticmethod
    def _extract_ovf_file_refs(ovf_content: str) -> list[str]:
        """
        Return all unique file references from an OVF descriptor.
        Captures any ovf:href value (vmdk, nvram, iso, etc.), excluding
        the OVF file itself and http(s) URLs (those are external references).
        """
        refs = re.findall(r'ovf:href="([^"]+)"', ovf_content)
        seen = set()
        result = []
        for ref in refs:
            if ref in seen:
                continue
            seen.add(ref)
            # Skip external URLs and the .ovf descriptor itself
            if ref.startswith('http://') or ref.startswith('https://'):
                continue
            if ref.endswith('.ovf') or ref.endswith('.mf') or ref.endswith('.cert'):
                continue
            result.append(ref)
        return result

    def _upload_ovf_files(self, session_id: str, source: str) -> None:
        """
        Upload OVF files to the update session.

        For remote URLs: uses PULL so vCenter downloads directly.
        For local files: reads and pushes content to vCenter.
        """
        is_local = os.path.exists(source)
        filename = os.path.basename(source)

        if is_local:
            if filename.endswith('.ova'):
                print(f"  Uploading local OVA {filename} ({os.path.getsize(source)} bytes)...")
                self._upload_file_from_path(session_id, filename, source)
            elif filename.endswith('.ovf'):
                base_dir = os.path.dirname(source)
                with open(source) as f:
                    ovf_content = f.read()
                self._upload_file_content(session_id, filename, ovf_content.encode())
                for ref in self._extract_ovf_file_refs(ovf_content):
                    ref_path = os.path.join(base_dir, ref)
                    if os.path.exists(ref_path):
                        print(f"  Uploading {ref} ({os.path.getsize(ref_path)} bytes)...")
                        self._upload_file_from_path(session_id, ref, ref_path)
                    else:
                        print(f"  Warning: referenced file not found locally, skipping: {ref}")
            else:
                raise ValueError(f"Unsupported file type: {filename}")
        else:
            # Remote URL - use PULL; pass the full cert chain so vCenter can verify the server
            ssl_chain = self._get_ssl_chain(source)
            if filename.endswith('.ova'):
                self._upload_file_from_url(session_id, source, filename, ssl_chain)
            elif filename.endswith('.ovf'):
                base_url = source.rsplit('/', 1)[0] + '/'
                print(f"  Downloading OVF descriptor from {source}...")
                response = requests.get(source, verify=False, timeout=60)
                response.raise_for_status()
                ovf_content = response.text
                self._upload_file_content(session_id, filename, ovf_content.encode())
                for ref in self._extract_ovf_file_refs(ovf_content):
                    ref_url = urljoin(base_url, ref)
                    print(f"  Adding file for transfer: {ref}")
                    self._upload_file_from_url(session_id, ref_url, ref, ssl_chain)
            else:
                raise ValueError(f"Unsupported file type: {filename}")


    def _get_ssl_chain(self, url: str) -> Optional[str]:
        """
        Fetch the full TLS certificate chain (leaf + intermediates) from the server
        in PEM format, concatenated.  vCenter needs the full chain to verify the server
        when performing PULL transfers.

        Uses `openssl s_client` to retrieve the chain; falls back to the leaf-only cert
        via the ssl module if openssl is not available.
        """
        import subprocess as _sp
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or 443

        print(f"  Fetching TLS certificate chain from {hostname}:{port}...")
        try:
            result = _sp.run(
                ["openssl", "s_client", "-connect", f"{hostname}:{port}",
                 "-showcerts", "-servername", hostname],
                input=b"",
                capture_output=True,
                timeout=15,
            )
            output = result.stdout.decode("utf-8", errors="replace")
            # Extract all PEM blocks from the output
            pem_blocks = re.findall(
                r"(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)",
                output, re.DOTALL
            )
            if pem_blocks:
                chain = "\n".join(pem_blocks)
                print(f"  Got {len(pem_blocks)} certificate(s) in chain for {hostname}")
                return chain
        except Exception as e:
            print(f"  Warning: openssl s_client failed ({e}), falling back to leaf cert")

        # Fallback: leaf cert only via ssl module
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            import socket as _sock
            with _sock.create_connection((hostname, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert_der = ssock.getpeercert(binary_form=True)
            cert_pem = ssl.DER_cert_to_PEM_cert(cert_der)
            print(f"  Got leaf certificate for {hostname}")
            return cert_pem
        except Exception as e:
            print(f"  Warning: Could not fetch SSL certificate: {e}")
            return None

    def _upload_file_from_url(self, session_id: str, file_url: str, filename: str,
                               ssl_cert: Optional[str] = None) -> None:
        """
        Register a file for PULL transfer in the update session.
        ssl_cert should be the full PEM chain so vCenter can verify the remote server.

        Falls back to PUSH (download locally then stream to vCenter) if vCenter
        returns a server-side error (e.g. 500 "transferSessionId is null") that
        indicates it cannot initiate the PULL transfer.
        """
        add_spec = {
            "name": filename,
            "source_type": "PULL",
            "source_endpoint": {
                "uri": file_url
            }
        }
        if ssl_cert:
            add_spec["source_endpoint"]["ssl_certificate"] = ssl_cert

        add_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}/file"
        response = self.rest_session.post(add_url, json=add_spec)
        if response.ok:
            print(f"  Added file {filename} (PULL from URL)")
            return

        # On server-side errors fall back to PUSH: download the file locally then stream it
        if response.status_code >= 500:
            print(f"  PULL failed ({response.status_code}), falling back to PUSH for {filename}")
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
                tmp_path = tmp.name
            try:
                dl = requests.get(file_url, verify=False, stream=True, timeout=300)
                dl.raise_for_status()
                with open(tmp_path, 'wb') as f:
                    for chunk in dl.iter_content(chunk_size=8 * 1024 * 1024):
                        f.write(chunk)
                self._upload_file_from_path(session_id, filename, tmp_path)
                print(f"  Uploaded file {filename} (PUSH fallback)")
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return

        raise RuntimeError(
            f"Failed to add file {filename} for PULL: "
            f"{response.status_code} {response.reason}\n{response.text}"
        )

    def _upload_file_content(self, session_id: str, filename: str, content: bytes) -> None:
        """Upload small file content (e.g. OVF descriptor) directly to the update session."""
        self._upload_file_path_or_bytes(session_id, filename, size=len(content), data=content)

    def _upload_file_from_path(self, session_id: str, filename: str, file_path: str) -> None:
        """Stream a local file to the update session without loading it into memory."""
        size = os.path.getsize(file_path)
        with open(file_path, 'rb') as f:
            self._upload_file_path_or_bytes(session_id, filename, size=size, data=f)

    def _upload_file_path_or_bytes(self, session_id: str, filename: str,
                                   size: int, data) -> None:
        """
        Core PUSH upload: register the file with the session then stream data to
        the upload endpoint. data may be bytes or an open file object.
        """
        add_spec = {
            "name": filename,
            "source_type": "PUSH",
            "size": size,
        }
        add_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}/file"
        response = self.rest_session.post(add_url, json=add_spec)
        if not response.ok:
            raise RuntimeError(
                f"Failed to add file {filename} for PUSH: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        file_info = response.json()

        upload_uri = file_info.get("upload_endpoint", {}).get("uri")
        if upload_uri:
            upload_response = self.rest_session.put(
                upload_uri,
                data=data,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                }
            )
            if not upload_response.ok:
                raise RuntimeError(
                    f"Failed to upload content for {filename}: "
                    f"{upload_response.status_code} {upload_response.reason}\n{upload_response.text}"
                )
        print(f"  Uploaded file {filename} ({size} bytes)")


class SupervisorClient:
    """Client for Supervisor operations via SSH."""

    def __init__(self, host: str, password: str, user: str = "root"):
        self.host = host
        self.user = user
        self.password = password
        self.ssh = None

    def connect(self) -> None:
        """Connect to Supervisor via SSH."""
        print(f"Connecting to Supervisor {self.host} via SSH...")
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(
            hostname=self.host,
            username=self.user,
            password=self.password,
            look_for_keys=False,
            allow_agent=False
        )
        print("  Connected to Supervisor")

    def disconnect(self) -> None:
        """Disconnect from Supervisor."""
        if self.ssh:
            self.ssh.close()
            print("Disconnected from Supervisor")

    def run_kubectl(self, args: str, check: bool = True) -> tuple[str, str, int]:
        """Run a kubectl command and return stdout, stderr, and return code."""
        cmd = f"kubectl {args}"
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()
        stdout_str = stdout.read().decode()
        stderr_str = stderr.read().decode()

        if check and exit_code != 0:
            raise RuntimeError(f"kubectl command failed: {cmd}\n{stderr_str}")

        return stdout_str, stderr_str, exit_code

    def namespace_exists(self, namespace: str) -> bool:
        """Check if a namespace exists."""
        _, _, exit_code = self.run_kubectl(f"get namespace {namespace}", check=False)
        return exit_code == 0

    def get_content_library(self, namespace: str, name: str) -> Optional[dict]:
        """Get content library info."""
        stdout, _, exit_code = self.run_kubectl(
            f"get contentlibrary -n {namespace} {name} -o json",
            check=False
        )
        if exit_code != 0:
            return None
        return json.loads(stdout)

    def list_content_libraries(self, namespace: str) -> list[str]:
        """List content libraries in a namespace."""
        stdout, _, _ = self.run_kubectl(
            f"get contentlibrary -n {namespace} -o jsonpath='{{.items[*].metadata.name}}'"
        )
        return stdout.strip("'").split() if stdout.strip("'") else []

    def wait_for_vmi(self, namespace: str, image_name: str,
                     timeout: int = VMI_WAIT_TIMEOUT) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Wait for a VirtualMachineImage to become Ready.

        Returns (vmi_name, None, None) on success.
        Returns (None, reason, last_seen_vmi_name) on terminal failure or timeout,
        where last_seen_vmi_name is the VMI CR name observed during polling (useful
        for fetching relevant vmop logs even on failure).
        """
        print(f"Waiting for VirtualMachineImage containing '{image_name}'...")
        start_time = time.time()
        last_vmi_msg = ""
        last_seen_vmi_name = ""

        while time.time() - start_time < timeout:
            stdout, _, _ = self.run_kubectl(
                f"get vmi -n {namespace} -o json",
                check=False
            )
            if stdout:
                try:
                    vmi_list = json.loads(stdout)
                    for item in vmi_list.get("items", []):
                        vmi_name = item.get("metadata", {}).get("name", "")
                        display_name = item.get("status", {}).get("name", "")
                        if image_name.lower() in vmi_name.lower() or image_name.lower() in display_name.lower():
                            last_seen_vmi_name = vmi_name
                            conditions = item.get("status", {}).get("conditions", [])
                            for cond in conditions:
                                if cond.get("type") == "Ready":
                                    if cond.get("status") == "True":
                                        print(f"  Found ready VMI: {vmi_name}")
                                        return vmi_name, None, None
                                    msg = cond.get("message", "")
                                    if msg:
                                        last_vmi_msg = msg
                                    # Only bail out on messages that indicate a permanent
                                    # vmop failure. Transient states ("cache not ready",
                                    # "VirtualMachineImageNotSynced" with no detail) are
                                    # normal during initial sync — keep polling.
                                    _TERMINAL_VMI_ERRORS = (
                                        "failed to get hardware",
                                        "failed to marshal",
                                        "failed to create or patch",
                                        "control characters are not allowed",
                                        "yaml:",
                                    )
                                    if msg and any(e in msg for e in _TERMINAL_VMI_ERRORS):
                                        reason = f"VirtualMachineImage not ready: {msg}"
                                        print(f"  VMI terminal error: {reason}")
                                        return None, reason, last_seen_vmi_name
                except json.JSONDecodeError:
                    pass

            print(f"  Waiting... ({int(time.time() - start_time)}s)")
            time.sleep(POLL_INTERVAL)

        timeout_reason = f"VirtualMachineImage for '{image_name}' did not become ready within {timeout}s"
        if last_vmi_msg:
            timeout_reason += f": {last_vmi_msg}"
        return None, timeout_reason, last_seen_vmi_name or None

    def create_vm(self, namespace: str, vm_name: str, image_name: str,
                  vm_class: str, storage_class: str,
                  ovf_info: Optional[OvfInfo] = None,
                  vapp_config: Optional[list] = None,
                  network_type: str = "nsx") -> None:
        """
        Create a VirtualMachine CR.

        Args:
            namespace: Kubernetes namespace
            vm_name: Name for the VM
            image_name: VirtualMachineImage name
            vm_class: VirtualMachineClass name
            storage_class: StorageClass name for the VM's disks
            ovf_info: Parsed OVF info for network interface population
            vapp_config: List of vAppConfig property dicts to inject directly into
                         spec.bootstrap.vAppConfig.properties. When provided, these
                         are used as-is instead of OVF-derived defaults.
        """
        spec: dict = {
            "className": vm_class,
            "imageName": image_name,
            "storageClass": storage_class,
            "powerState": "PoweredOn",
        }
        if not (ovf_info and ovf_info.guest_id):
            spec["guestID"] = "vmwarePhoton64Guest"

        # Add network interfaces from OVF network definitions
        if ovf_info and ovf_info.has_networks():
            interfaces = []
            for i, net in enumerate(ovf_info.networks):
                if network_type == "vds":
                    net_ref = {
                        "apiVersion": "netoperator.vmware.com/v1alpha1",
                        "kind": "Network",
                        "name": ""
                    }
                else:  # nsx (default)
                    net_ref = {
                        "apiVersion": "crd.nsx.vmware.com/v1alpha1",
                        "kind": "SubnetSet",
                        "name": ""
                    }
                interfaces.append({"name": f"eth{i}", "network": net_ref})
            if interfaces:
                spec["network"] = {"interfaces": interfaces}

        # vAppConfig: use provided config directly, or fall back to OVF defaults
        if vapp_config:
            spec["bootstrap"] = {
                "vAppConfig": {"properties": vapp_config}
            }
        elif ovf_info and ovf_info.has_properties():
            props = auto_fill_vapp_properties(ovf_info)
            spec["bootstrap"] = {
                "vAppConfig": {"properties": props}
            }

        vm_manifest = {
            "apiVersion": "vmoperator.vmware.com/v1alpha6",
            "kind": "VirtualMachine",
            "metadata": {
                "name": vm_name,
                "namespace": namespace
            },
            "spec": spec
        }

        yaml_content = yaml.dump(vm_manifest, default_flow_style=False)
        print(f"Creating VirtualMachine {vm_name}...")
        print(f"  Spec:\n{yaml_content}")

        # Apply via kubectl
        cmd = f"cat <<'VMEOF' | kubectl apply -f -\n{yaml_content}\nVMEOF"
        stdin, stdout, stderr = self.ssh.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()

        if exit_code != 0:
            raise RuntimeError(f"Failed to create VM: {stderr.read().decode()}")

        print(f"  VirtualMachine {vm_name} created")

    def wait_for_vm_powered_on(self, namespace: str, vm_name: str,
                               vcenter: Optional[Any] = None,
                               timeout: int = VM_TOOLS_WAIT_TIMEOUT) -> tuple[bool, dict]:
        """
        Wait for the VM to be powered on.

        Power state is checked via the vSphere API (vcenter.is_vm_powered_on) when
        vcenter is provided, falling back to status.powerState in the CR otherwise.
        The CR is still polled each iteration to detect terminal error conditions early.

        Returns (powered_on, vm_status_dict).
        """
        print(f"Waiting for {vm_name} to power on...")
        start_time = time.time()
        last_status: dict = {}

        while time.time() - start_time < timeout:
            stdout, _, _ = self.run_kubectl(
                f"get vm -n {namespace} {vm_name} -o json",
                check=False
            )
            if stdout:
                try:
                    vm_cr = json.loads(stdout)
                    last_status = vm_cr.get("status", {})
                    phase = last_status.get("phase", "Unknown")
                    conditions = last_status.get("conditions", [])

                    # Bail out immediately on any terminal error condition
                    error_conditions = [
                        c for c in conditions
                        if c.get("reason") == "Error" and c.get("status") == "False"
                    ]
                    if error_conditions:
                        msgs = "; ".join(
                            f"{c.get('type')}: {c.get('message', '')}"
                            for c in error_conditions
                        )
                        print(f"  Terminal error condition(s) detected: {msgs}")
                        return False, last_status

                    # Check power state via vSphere API if available, else fall back to CR
                    if vcenter is not None:
                        powered_on = vcenter.is_vm_powered_on(vm_name)
                    else:
                        powered_on = last_status.get("powerState", "") == "PoweredOn"

                    if powered_on:
                        print(f"  VM is powered on")
                        return True, last_status

                    print(f"  Waiting... ({int(time.time() - start_time)}s) - Phase: {phase}")
                except json.JSONDecodeError:
                    pass
            time.sleep(POLL_INTERVAL)

        return False, last_status

    def get_vm_status_reason(self, namespace: str, vm_name: str) -> str:
        """
        Extract a human-readable reason from the VM CR status conditions.

        Looks at .status.conditions for any False or error conditions and
        returns their message, falling back to phase if nothing useful found.
        """
        stdout, _, _ = self.run_kubectl(
            f"get vm -n {namespace} {vm_name} -o json",
            check=False
        )
        if not stdout:
            return "VM not found"
        try:
            vm = json.loads(stdout)
            status = vm.get("status", {})
            phase = status.get("phase", "Unknown")
            conditions = status.get("conditions", [])
            reasons = []
            for cond in conditions:
                if cond.get("status") != "True":
                    msg = cond.get("message", "")
                    reason = cond.get("reason", "")
                    ctype = cond.get("type", "")
                    part = f"{ctype}: {reason}"
                    if msg:
                        part += f" - {msg}"
                    reasons.append(part)
            if reasons:
                return f"Phase={phase}; " + "; ".join(reasons)
            return f"Phase={phase}"
        except json.JSONDecodeError:
            return "Could not parse VM status"

    def get_vmop_logs(self, *search_terms: str, since_seconds: Optional[int] = None) -> str:
        """
        Fetch vmop controller-manager logs with lines matching ANY of the search terms.

        When since_seconds is provided, fetches logs from that many seconds ago
        (more reliable than --tail for long runs). Falls back to --tail=500.

        Returns relevant log lines as a single string, empty if none found.
        """
        if not search_terms:
            return ""
        if since_seconds is not None:
            log_arg = f"--since={since_seconds}s"
        else:
            log_arg = "--tail=500"
        # Escape regex special chars in each term, join with | for alternation
        escaped = "|".join(
            t.replace("\\", "\\\\").replace(".", r"\.").replace("[", r"\[").replace("]", r"\]")
            for t in search_terms
        )
        stdout, _, _ = self.run_kubectl(
            f"logs deploy/vmware-system-vmop-controller-manager "
            f"-n vmware-system-vmop {log_arg} 2>/dev/null | grep -E '{escaped}' || true",
            check=False
        )
        return stdout.strip() if stdout else ""

    def get_vmop_logs_for_vm(self, vm_name: str, since_seconds: Optional[int] = None) -> str:
        """Fetch vmop logs relevant to a VirtualMachine by name."""
        return self.get_vmop_logs(vm_name, since_seconds=since_seconds)

    def delete_vm(self, namespace: str, vm_name: str) -> None:
        """Delete a VirtualMachine."""
        self.run_kubectl(f"delete vm -n {namespace} {vm_name} --ignore-not-found")
        print(f"  Deleted VM {vm_name}")


def discover_ovfs(base_url: str) -> list[str]:
    """
    Discover OVF/OVA files from an Artifactory repository URL.

    Uses the Artifactory UI native browser API which returns all entries
    including remote/uncached ones that the storage API misses. Directory
    traversal is parallelised with a thread pool for speed.

    Args:
        base_url: Artifactory UI or download base URL (e.g. ending in testdata/)

    Returns:
        Sorted list of OVF/OVA download URLs
    """
    import concurrent.futures
    import threading

    print(f"Discovering OVFs from {base_url}...")

    # Derive host and repo/path to build the UI native browser API URL.
    # Accepted input forms:
    #   https://host/ui/native/repo/path/
    #   https://host/artifactory/repo/path/
    parsed = urlparse(base_url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    if "/ui/native/" in path:
        repo_path = path.split("/ui/native/", 1)[1]
    elif "/artifactory/" in path:
        repo_path = path.split("/artifactory/", 1)[1]
    else:
        repo_path = path.lstrip("/")

    ui_base = f"{host}/ui/api/v1/ui/nativeBrowser/{repo_path}"
    dl_base = f"{host}/artifactory/{repo_path}"

    ovfs: list[str] = []
    lock = threading.Lock()

    def browse(ui_path: str, dl_path: str,
               executor: "concurrent.futures.ThreadPoolExecutor",
               futures: list) -> None:
        try:
            r = requests.get(ui_path, headers={"Accept": "application/json"},
                             verify=False, timeout=30)
            r.raise_for_status()
            data = r.json()
            for child in data.get("children", []):
                name = child.get("name", "")
                if child.get("folder"):
                    child_ui = ui_path.rstrip("/") + "/" + name
                    child_dl = dl_path.rstrip("/") + "/" + name
                    f = executor.submit(browse, child_ui, child_dl, executor, futures)
                    with lock:
                        futures.append(f)
                elif name.lower().endswith((".ovf", ".ova")) and not name.startswith("._"):
                    with lock:
                        ovfs.append(dl_path.rstrip("/") + "/" + name)
        except Exception as e:
            print(f"  Warning: Could not browse {ui_path}: {e}")

    futures: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures.append(executor.submit(browse, ui_base, dl_base, executor, futures))
        i = 0
        while i < len(futures):
            futures[i].result()
            i += 1

    ovfs.sort()
    print(f"  Found {len(ovfs)} OVF/OVA files")
    return ovfs


@dataclass
class OvfEntry:
    """A single OVF/OVA to deploy."""
    name: str
    source: str
    config_file: Optional[str] = None  # Path to YAML file with vAppConfig properties


@dataclass
class DeployResult:
    """Result of deploying a single OVF/OVA."""
    name: str
    source: str
    vm_name: str
    status: str          # SUCCESS / PARTIAL / FAILED
    reason: str          # Human-readable explanation
    vmop_logs: str = ""  # Relevant vmop log lines


def load_ovf_list(path: str) -> list[OvfEntry]:
    """
    Load OVF entries from a CSV file.

    Format: name,source[,config_file]
      name        - item/VM name
      source      - local file path or remote URL to OVF/OVA
      config_file - optional path to YAML file with vAppConfig properties

    Lines starting with '#' are treated as comments.
    """
    entries = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',', 2)]
            if len(parts) < 2:
                print(f"  Warning: Skipping malformed line {lineno} in {path}: {line!r}")
                continue
            name, source = parts[0], parts[1]
            config_file = parts[2] if len(parts) == 3 and parts[2] else None
            if name and source:
                entries.append(OvfEntry(name=name, source=source, config_file=config_file))
    print(f"Loaded {len(entries)} OVF entries from {path}")
    return entries


def _smart_value_for_property(prop: OvfProperty) -> str:
    """
    Return a smart value for an OVF property using Go template expressions
    where the semantics are clear from the key/label/description/type, or a
    random-but-valid fallback otherwise.

    Template expressions reference the VM Operator V1alpha6 bootstrap context
    (same as vc_vappconfig.yaml).
    """
    import random
    import string

    key = prop.key.lower()
    label = prop.label.lower()
    desc = prop.description.lower()
    combined = f"{key} {label} {desc}"
    typ = prop.type.lower()

    # --- OVF type-based rules ---
    if typ == "boolean":
        default = prop.default.capitalize() if prop.default else ""
        return default if default in ("True", "False") else "False"

    # Non-user-configurable with a default: keep it as-is.
    if not prop.user_configurable and prop.default:
        return prop.default

    if typ == "password":
        # Generate a random password that satisfies common complexity rules.
        chars = string.ascii_letters + string.digits + "!@#$"
        return "VMware1!" + "".join(random.choices(chars, k=8))

    if typ == "ip":
        return '{{ V1alpha6_FormatIP (index (index .V1alpha6.Net.Devices 0).IPAddresses 0) "" }}'

    # --- Key/label/description pattern matching ---

    # IPv6-specific fields: leave empty — we don't configure IPv6 addresses
    if any(x in combined for x in ("ipv6", "ip6", "inet6")):
        return ""

    # Subnet prefix length (numeric, e.g. "24") — must not also match "netmask"
    if any(x in combined for x in ("prefixlen", "prefix_len", "prefix-len")) or \
       (any(x in combined for x in ("prefix", "cidr")) and
            not any(x in combined for x in ("netmask", "subnet mask", "subnetmask"))):
        return '{{ V1alpha6_SubnetPrefixLength (index (index .V1alpha6.Net.Devices 0).IPAddresses 0) }}'

    # Subnet mask (dotted-decimal, e.g. "255.255.255.0")
    if any(x in combined for x in ("netmask", "subnet mask", "subnetmask", "net.mask", "net_mask")):
        return '{{ V1alpha6_SubnetMask (index (index .V1alpha6.Net.Devices 0).IPAddresses 0) }}'

    # IP address (but not gateway/dns)
    if any(x in combined for x in ("ip address", "ip_address", "ipaddress", "net.addr",
                                    "net_addr", "nsx_ip", "mgmt_ip", "management ip",
                                    "pnid", "hostname")) and \
       not any(x in combined for x in ("gateway", "dns", "nameserver")):
        return '{{ V1alpha6_FormatIP (index (index .V1alpha6.Net.Devices 0).IPAddresses 0) "" }}'

    # Gateway
    if any(x in combined for x in ("gateway", "default route", "net.gateway", "net_gateway")):
        return "{{ (index .V1alpha6.Net.Devices 0).Gateway4 }}"

    # DNS / nameservers
    if any(x in combined for x in ("dns", "nameserver", "name server", "net.dns")):
        return '{{ V1alpha6_FormatNameservers -1 "," }}'

    # Domain / search path
    if any(x in combined for x in ("domain", "searchpath", "search path", "search_path", "dnsdomain")):
        return "lvn.broadcom.net"

    # Password fields by key/label
    if any(x in combined for x in ("password", "passwd", "secret", "credential")):
        chars = string.ascii_letters + string.digits + "!@#$"
        return "VMware1!" + "".join(random.choices(chars, k=8))

    # Use the OVF default if one exists
    if prop.default:
        return prop.default

    # Generic string fallback: random alphanumeric
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def auto_fill_vapp_properties(ovf_info: OvfInfo) -> list[dict]:
    """
    Build a vAppConfig properties list from OVF property definitions,
    filling each value with a smart template expression or random fallback.
    Only user-configurable properties (or those with no default) are filled;
    non-user-configurable properties with defaults keep their defaults.
    """
    result = []
    for prop in ovf_info.properties:
        value = _smart_value_for_property(prop)
        result.append({"key": prop.key, "value": {"value": value}})
        print(f"    auto-fill: {prop.key!r} = {value!r}")
    return result


def load_vapp_config(config_file: str) -> list[dict]:
    """
    Load vAppConfig properties from a YAML file.

    The file should contain a 'properties' list in the same format used
    in the VirtualMachine CR spec.bootstrap.vAppConfig.properties, e.g.:

      properties:
        - key: nsx_hostname
          value:
            value: nsx-manager-1
        - key: nsx_passwd_0
          value:
            value: VMware1!VMware1!
    """
    with open(config_file) as f:
        data = yaml.safe_load(f)
    props = data.get("properties", [])
    print(f"  Loaded {len(props)} vApp properties from {config_file}")
    return props


def ovf_list_from_discovered(urls: list[str]) -> list[OvfEntry]:
    """
    Convert a list of discovered OVF/OVA URLs into OvfEntry objects.

    When multiple URLs share the same filename stem (e.g. nostalgia.ovf in
    different directories), the name is disambiguated by prepending the
    immediate parent directory: nostalgia/nostalgia, nostalgia_small/nostalgia, etc.
    """
    # First pass: collect raw stem → list of urls
    from collections import defaultdict
    stem_to_urls: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        filename = os.path.basename(urlparse(url).path)
        stem = os.path.splitext(filename)[0]
        stem_to_urls[stem].append(url)

    entries = []
    for url in urls:
        parsed = urlparse(url)
        path_parts = parsed.path.rstrip("/").split("/")
        filename = path_parts[-1]
        stem = os.path.splitext(filename)[0]

        if len(stem_to_urls[stem]) > 1:
            # Walk up path segments until we find one that makes the name unique,
            # or use up to 2 levels if all collide.
            for depth in range(1, min(3, len(path_parts))):
                parent = path_parts[-(depth + 1)]
                candidate = f"{parent}/{stem}"
                # Unique if no other URL in the same stem group produces the same candidate
                others = [u for u in stem_to_urls[stem] if u != url]
                if not any(
                    urlparse(u).path.rstrip("/").split("/")[-(depth + 1)] == parent
                    for u in others
                ):
                    name = candidate.replace("/", "_")
                    break
            else:
                # Full path fallback: use everything after base_path separator
                name = "_".join(path_parts[-3:-1]) + "_" + stem
        else:
            name = stem

        entries.append(OvfEntry(name=name, source=url))
    return entries


def fetch_ovf_info(source: str) -> Optional[OvfInfo]:
    """Parse OVF info from a local file path or remote URL."""
    if os.path.exists(source):
        return fetch_ovf_from_file(source)
    return fetch_ovf_from_url(source)


_STATUS_STYLE = {
    "SUCCESS":      ("✅", "#1a7f37", "#dafbe1"),
    "FAILED":       ("❌", "#cf222e", "#ffebe9"),
    "SETUP_FAILED": ("🔧", "#8250df", "#fbefff"),
    "SKIPPED":      ("⏭",  "#57606a", "#f6f8fa"),
}


def _html_escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def write_report(results: list[DeployResult], report_path: str) -> None:
    """Write an HTML deployment results report and print a summary to stdout."""
    # Ensure the output path ends in .html
    if not report_path.endswith(".html"):
        report_path = os.path.splitext(report_path)[0] + ".html"

    generated = time.strftime("%Y-%m-%d %H:%M:%S")

    # --- Build table rows ---
    rows_html = []
    for r in results:
        icon, fg, bg = _STATUS_STYLE.get(r.status, ("•", "#24292f", "#f6f8fa"))
        name_cell = (
            f'<a href="{_html_escape(r.source)}" target="_blank">'
            f'{_html_escape(r.name)}</a>'
            if r.source.startswith("http")
            else _html_escape(r.name)
        )
        badge = (
            f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
            f'background:{bg};color:{fg};font-weight:600;font-size:0.85em;'
            f'border:1px solid {fg}33;">{icon} {_html_escape(r.status)}</span>'
        )
        reason_escaped = _html_escape(r.reason)
        logs_cell = ""
        if r.vmop_logs:
            logs_escaped = _html_escape(r.vmop_logs)
            logs_cell = (
                f'<details><summary style="cursor:pointer;color:#57606a;font-size:0.8em;">'
                f'vmop logs</summary>'
                f'<pre style="font-size:0.75em;background:#f6f8fa;padding:8px;'
                f'border-radius:4px;overflow:auto;max-height:300px;">'
                f'{logs_escaped}</pre></details>'
            )
        rows_html.append(
            f"<tr>"
            f'<td style="padding:8px 12px;">{name_cell}</td>'
            f'<td style="padding:8px 12px;font-family:monospace;font-size:0.9em;">'
            f'{_html_escape(r.vm_name)}</td>'
            f'<td style="padding:8px 12px;text-align:center;">{badge}</td>'
            f'<td style="padding:8px 12px;font-size:0.85em;color:#24292f;">'
            f'{reason_escaped}{("<br>" + logs_cell) if logs_cell else ""}</td>'
            f"</tr>"
        )

    # --- Summary counts ---
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary_parts = []
    for status, (icon, fg, bg) in _STATUS_STYLE.items():
        n = counts.get(status, 0)
        if n:
            summary_parts.append(
                f'<span style="margin-right:16px;color:{fg};font-weight:600;">'
                f'{icon} {status}: {n}</span>'
            )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OVF Deploy Report — {generated}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 32px; color: #24292f; background: #fff; }}
  h1   {{ font-size: 1.4em; margin-bottom: 4px; }}
  .meta {{ color: #57606a; font-size: 0.85em; margin-bottom: 20px; }}
  .summary {{ margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9em; }}
  thead th {{ background: #f6f8fa; border-bottom: 2px solid #d0d7de;
              padding: 8px 12px; text-align: left; font-weight: 600; }}
  tbody tr:nth-child(even) {{ background: #f6f8fa; }}
  tbody tr:hover {{ background: #eaf5ff; }}
  td, th {{ border: 1px solid #d0d7de; vertical-align: top; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>OVF Deploy Test Report</h1>
<div class="meta">Generated: {generated} &nbsp;|&nbsp; Total: {len(results)}</div>
<div class="summary">{"".join(summary_parts)}</div>
<table>
<thead>
  <tr>
    <th>OVF Name</th>
    <th>VM Name</th>
    <th>Status</th>
    <th>Details</th>
  </tr>
</thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table>
</body>
</html>
"""

    with open(report_path, "w") as f:
        f.write(html)

    # Print a plain-text summary to stdout
    print(f"\n{'='*60}")
    print(f"OVF Deploy Report — {generated}")
    print(f"{'='*60}")
    col = max((len(r.name) for r in results), default=10)
    for r in results:
        icon = _STATUS_STYLE.get(r.status, ("•",))[0]
        print(f"  {icon} {r.name:<{col}}  {r.status:<12}  {r.reason[:80]}")
    print(f"\n{'='*60}")
    for status, n in sorted(counts.items()):
        icon = _STATUS_STYLE.get(status, ("•",))[0]
        print(f"  {icon} {status}: {n}")
    print(f"\nReport written to {report_path}")


def vm_name_from_item(item_name: str) -> str:
    """
    Derive a short, K8s-safe VM name from the content library item name.

    K8s names must be lowercase alphanumeric or '-', max 63 chars.
    e.g. "BIGIP-16.0.1.1-0.0.6.ALL-vmware" -> "bigip"
         "bitnami-tomcatstack-8.0.36-x86_64" -> "bitnami-tomcatstack"
         "SLES11_SP2_64"                     -> "sles11"
    """
    # Lowercase and replace underscores with dashes
    name = item_name.lower().replace("_", "-")
    # Strip everything from the first version-like segment (digit after separator)
    name = re.split(r'[-.](\d)', name)[0]
    # Keep only alphanumeric and dashes, collapse consecutive dashes
    name = re.sub(r'[^a-z0-9-]', '-', name)
    name = re.sub(r'-+', '-', name).strip('-')
    # Truncate to 40 chars to leave room for suffix if needed
    return name[:40]



def _add_vcenter_args(parser: argparse.ArgumentParser) -> None:
    """Add common vCenter connection arguments to a subcommand parser."""
    parser.add_argument("--vcenter", required=True, help="vCenter hostname or IP")
    parser.add_argument("--vcenter-user", default=DEFAULT_VCENTER_USER,
                        help=f"vCenter username (default: {DEFAULT_VCENTER_USER})")
    parser.add_argument("--vcenter-password", required=True, help="vCenter password")
    parser.add_argument("--vcenter-root-password",
                        help="vCenter root SSH password for decryptK8Pwd.py (default: same as --vcenter-password)")


def cmd_discover(args: argparse.Namespace) -> int:
    """
    Discover OVF/OVA files from Artifactory and write a CSV ready for 'deploy'.

    Output format: name,url  (one entry per line, commented header included)
    """
    urls = discover_ovfs(args.base_url)
    if not urls:
        print("No OVF/OVA files found.")
        return 1

    entries = ovf_list_from_discovered(urls)

    out_path = args.output
    with open(out_path, 'w') as f:
        f.write(f"# OVF deploy list - generated from {args.base_url}\n")
        f.write("# Format: name,source[,config_file]\n")
        for entry in entries:
            f.write(f"{entry.name},{entry.source}\n")

    print(f"Wrote {len(entries)} entries to {out_path}")
    print(f"Edit the file to add a config_file column where needed, then run 'deploy'.")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    """Deploy OVFs listed in a CSV file via VM Service."""
    entries = load_ovf_list(args.csv)
    if not entries:
        print(f"ERROR: No valid entries found in {args.csv}")
        return 1

    vcenter = None
    supervisor = None

    try:
        vcenter = VCenterClient(
            args.vcenter,
            args.vcenter_user,
            args.vcenter_password,
            args.vcenter_root_password
        )
        vcenter.connect()

        supervisor_ip, supervisor_password = vcenter.get_supervisor_credentials()
        if args.supervisor_root_password:
            supervisor_password = args.supervisor_root_password
            print("  Using provided Supervisor root password override")

        library_id = vcenter.find_content_library(args.content_library)
        if not library_id:
            print(f"ERROR: Content library '{args.content_library}' not found in vCenter")
            return 1

        supervisor = SupervisorClient(supervisor_ip, supervisor_password)
        supervisor.connect()

        if not supervisor.namespace_exists(args.namespace):
            print(f"ERROR: Namespace '{args.namespace}' does not exist")
            return 1
        print(f"Namespace '{args.namespace}' exists")

        libraries = supervisor.list_content_libraries(args.namespace)
        print(f"Content libraries in namespace: {libraries}")

        results: list[DeployResult] = []
        report_path = args.report or (os.path.splitext(args.csv)[0] + ".report.html")

        def record(result: DeployResult) -> None:
            results.append(result)
            write_report(results, report_path)

        for entry in entries:
            print(f"\n{'=' * 60}")
            print(f"Deploying: {entry.name}  ({entry.source})")
            print(f"{'=' * 60}")
            vm_name = vm_name_from_item(entry.name)
            try:
                item_name = entry.name
                print(f"  Item name: '{item_name}' -> VM name: '{vm_name}'")

                print("  Parsing OVF descriptor...")
                ovf_info = fetch_ovf_info(entry.source)
                if ovf_info:
                    if ovf_info.is_vapp:
                        print("    Type: vApp (VirtualSystemCollection) - skipping, not supported by VM Service")
                        record(DeployResult(
                            name=entry.name, source=entry.source, vm_name=vm_name,
                            status="SKIPPED", reason="Multi-VM OVF not supported by VM Service"
                        ))
                        continue
                    if ovf_info.has_networks():
                        print(f"    Networks: {[n.name for n in ovf_info.networks]}")
                    if ovf_info.has_properties():
                        print(f"    vApp properties: {len(ovf_info.properties)} keys")
                else:
                    print("    Warning: Could not parse OVF, proceeding without network/property info")

                vapp_config = None
                if entry.config_file:
                    vapp_config = load_vapp_config(entry.config_file)

                try:
                    vcenter.upload_ovf(library_id, entry.source, item_name)
                except UntrustedSourceError:
                    raise
                except Exception as upload_err:
                    raise SetupError(f"Content library upload failed: {upload_err}") from upload_err

                vmi_name, vmi_error, failed_vmi_name = supervisor.wait_for_vmi(args.namespace, item_name)
                if not vmi_name:
                    # Search logs by VMI CR name (e.g. vmi-abc123) if we saw it, plus item name
                    search_terms = [t for t in [failed_vmi_name, item_name] if t]
                    logs = supervisor.get_vmop_logs(*search_terms)
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="FAILED",
                        reason=vmi_error or f"VirtualMachineImage for '{item_name}' never appeared",
                        vmop_logs=logs
                    ))
                    continue

                _, _, rc = supervisor.run_kubectl(
                    f"get vm -n {args.namespace} {vm_name}", check=False
                )
                if rc == 0:
                    print(f"  VM '{vm_name}' already exists, deleting...")
                    supervisor.delete_vm(args.namespace, vm_name)
                    for _ in range(30):
                        _, _, rc = supervisor.run_kubectl(
                            f"get vm -n {args.namespace} {vm_name}", check=False
                        )
                        if rc != 0:
                            break
                        time.sleep(5)
                    else:
                        print("  Warning: VM deletion timed out, proceeding anyway")

                vm_start_time = time.time()
                supervisor.create_vm(
                    namespace=args.namespace,
                    vm_name=vm_name,
                    image_name=vmi_name,
                    vm_class=args.vm_class,
                    storage_class=args.storage_class,
                    ovf_info=ovf_info,
                    network_type=args.network_type,
                    vapp_config=vapp_config,
                )

                powered_on, _ = supervisor.wait_for_vm_powered_on(args.namespace, vm_name, vcenter=vcenter)

                if powered_on:
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="SUCCESS", reason="VM powered on"
                    ))
                else:
                    elapsed = int(time.time() - vm_start_time) + 30  # 30s buffer
                    reason = supervisor.get_vm_status_reason(args.namespace, vm_name)
                    logs = supervisor.get_vmop_logs_for_vm(vm_name, since_seconds=elapsed)
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="FAILED",
                        reason=f"VM did not reach Running phase within timeout. {reason}",
                        vmop_logs=logs
                    ))

                if args.cleanup:
                    try:
                        supervisor.delete_vm(args.namespace, vm_name)
                    except Exception as e:
                        print(f"  Warning: cleanup VM failed: {e}")
                    try:
                        cl_item = vcenter.find_library_item(library_id, item_name)
                        if cl_item:
                            vcenter.delete_library_item(cl_item["id"])
                            print(f"  Deleted content library item '{item_name}'")
                    except Exception as e:
                        print(f"  Warning: cleanup CL item failed: {e}")

            except UntrustedSourceError as e:
                print(f"  Skipping: source server TLS certificate is expired or not trusted by vCenter")
                record(DeployResult(
                    name=entry.name, source=entry.source, vm_name=vm_name,
                    status="SKIPPED", reason="Source TLS certificate expired or not trusted by vCenter"
                ))

            except SetupError as e:
                print(f"  Setup failed: {e}")
                record(DeployResult(
                    name=entry.name, source=entry.source, vm_name=vm_name,
                    status="SETUP_FAILED", reason=str(e)
                ))

            except Exception as e:
                import traceback
                traceback.print_exc()
                # Best-effort: grab vmop logs even on exception
                try:
                    logs = supervisor.get_vmop_logs_for_vm(vm_name) if supervisor else ""
                    reason_from_cr = supervisor.get_vm_status_reason(args.namespace, vm_name) if supervisor else ""
                except Exception:
                    logs, reason_from_cr = "", ""
                reason = f"{e}"
                if reason_from_cr:
                    reason += f". CR: {reason_from_cr}"
                record(DeployResult(
                    name=entry.name, source=entry.source, vm_name=vm_name,
                    status="FAILED", reason=reason, vmop_logs=logs
                ))

        write_report(results, report_path)  # final write also prints summary to stdout
        return 1 if any(r.status in ("FAILED", "SETUP_FAILED") for r in results) else 0

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if supervisor:
            supervisor.disconnect()
        if vcenter:
            vcenter.disconnect()


def cmd_validate(args: argparse.Namespace) -> int:
    """
    Validate OVFs by uploading to a content library and deploying directly via
    vSphere API (no VM Service / Supervisor required). Each VM is deleted after
    the test regardless of outcome.
    """
    entries = load_ovf_list(args.csv)
    if not entries:
        print(f"ERROR: No valid entries found in {args.csv}")
        return 1

    report_path = args.report or (os.path.splitext(args.csv)[0] + ".report.html")
    results: list[DeployResult] = []

    def record(result: DeployResult) -> None:
        results.append(result)
        write_report(results, report_path)

    vcenter: Optional[VCenterClient] = None
    try:
        vcenter = VCenterClient(
            args.vcenter,
            args.vcenter_user,
            args.vcenter_password,
            args.vcenter_root_password,
        )
        vcenter.connect()

        library_id = vcenter.find_content_library(args.content_library)
        if not library_id:
            print(f"ERROR: Content library '{args.content_library}' not found")
            return 1

        target = vcenter.get_default_deploy_target(
            datacenter=args.datacenter,
            cluster=args.cluster,
            datastore=args.datastore,
        )

        for entry in entries:
            vm_name = vm_name_from_item(entry.name)
            item_name = entry.name
            print(f"\n[{entry.name}] source={entry.source}")

            vm_id: Optional[str] = None
            try:
                ovf_info = fetch_ovf_info(entry.source)
                if ovf_info and ovf_info.is_vapp:
                    print("    Type: vApp — skipping, not supported")
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="SKIPPED", reason="Multi-VM OVF not supported by VM Service"
                    ))
                    continue

                try:
                    vcenter.upload_ovf(library_id, entry.source, item_name)
                except UntrustedSourceError:
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="SKIPPED",
                        reason="Source TLS certificate expired or not trusted by vCenter"
                    ))
                    continue
                except Exception as e:
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="SETUP_FAILED",
                        reason=f"Content library upload failed: {e}"
                    ))
                    continue

                cl_item = vcenter.find_library_item(library_id, item_name)
                if not cl_item:
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="FAILED", reason="Library item not found after upload"
                    ))
                    continue

                vm_id = vcenter.deploy_library_item(
                    cl_item["id"], vm_name,
                    target["resource_pool_id"],
                    target["folder_id"],
                    target["datastore_id"],
                )

                vcenter.power_on_vm_by_id(vm_id)
                powered_on = vcenter.wait_for_vm_powered_on_by_id(vm_id)

                if powered_on:
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="SUCCESS", reason="VM powered on"
                    ))
                else:
                    record(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="FAILED",
                        reason="VM did not reach PoweredOn state within timeout"
                    ))

            except Exception as e:
                import traceback
                traceback.print_exc()
                record(DeployResult(
                    name=entry.name, source=entry.source, vm_name=vm_name,
                    status="FAILED", reason=str(e)
                ))
            finally:
                # Always clean up — this is a validation run
                if vm_id:
                    vcenter.delete_vm_by_id(vm_id)
                try:
                    cl_item = vcenter.find_library_item(library_id, item_name)
                    if cl_item:
                        vcenter.delete_library_item(cl_item["id"])
                        print(f"  Deleted content library item '{item_name}'")
                except Exception as e:
                    print(f"  Warning: could not delete CL item '{item_name}': {e}")

        write_report(results, report_path)
        return 1 if any(r.status in ("FAILED", "SETUP_FAILED") for r in results) else 0

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if vcenter:
            vcenter.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OVF deploy test tool for VM Service on Supervisor"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- discover subcommand ---
    p_discover = sub.add_parser(
        "discover",
        help="Discover OVF/OVA files from Artifactory and write a deploy CSV"
    )
    p_discover.add_argument(
        "output",
        help="Path to write the CSV file (e.g. ovfs.csv)"
    )
    p_discover.add_argument(
        "--base-url",
        default=DEFAULT_OVF_BASE_URL,
        help=f"Artifactory base URL to discover from (default: {DEFAULT_OVF_BASE_URL})"
    )

    # --- deploy subcommand ---
    p_deploy = sub.add_parser(
        "deploy",
        help="Deploy OVFs from a CSV file via VM Service"
    )
    p_deploy.add_argument(
        "csv",
        help="CSV file with OVFs to deploy (name,source[,config_file])"
    )
    _add_vcenter_args(p_deploy)
    p_deploy.add_argument(
        "--supervisor-root-password",
        help="Override Supervisor root SSH password (default: retrieved from vCenter)"
    )
    p_deploy.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"Target namespace (default: {DEFAULT_NAMESPACE})"
    )
    p_deploy.add_argument(
        "--content-library",
        default=DEFAULT_CONTENT_LIBRARY,
        help=f"Content library name (default: {DEFAULT_CONTENT_LIBRARY})"
    )
    p_deploy.add_argument(
        "--vm-class",
        default=DEFAULT_VM_CLASS,
        help=f"VM class to use (default: {DEFAULT_VM_CLASS})"
    )
    p_deploy.add_argument(
        "--storage-class",
        default=STORAGE_CLASS,
        help=f"Storage class for VM disks (default: {STORAGE_CLASS})"
    )
    p_deploy.add_argument(
        "--network-type",
        choices=["nsx", "vds"],
        default="nsx",
        help="Network type: 'nsx' (SubnetSet) or 'vds' (Network) (default: nsx)"
    )
    p_deploy.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete each VM and its content library item after deployment (errors ignored)"
    )
    p_deploy.add_argument(
        "--report",
        help="Path to write the results report (default: <csv>.report.html)"
    )

    # --- validate subcommand ---
    p_validate = sub.add_parser(
        "validate",
        help="Validate OVFs by deploying directly via vSphere API (no Supervisor needed)"
    )
    p_validate.add_argument(
        "csv",
        help="CSV file with OVFs to validate (name,source[,config_file])"
    )
    _add_vcenter_args(p_validate)
    p_validate.add_argument(
        "--content-library",
        default=DEFAULT_CONTENT_LIBRARY,
        help=f"Content library name to use for staging (default: {DEFAULT_CONTENT_LIBRARY})"
    )
    p_validate.add_argument(
        "--datacenter",
        default=None,
        help="Datacenter name to deploy into (default: first available)"
    )
    p_validate.add_argument(
        "--cluster",
        default=None,
        help="Cluster name to deploy into (default: first available)"
    )
    p_validate.add_argument(
        "--datastore",
        default=None,
        help="Datastore name to deploy into (default: first writable)"
    )
    p_validate.add_argument(
        "--report",
        help="Path to write the results report (default: <csv>.report.html)"
    )

    args = parser.parse_args()

    if args.command == "discover":
        return cmd_discover(args)
    elif args.command == "validate":
        return cmd_validate(args)
    else:
        return cmd_deploy(args)


if __name__ == "__main__":
    sys.exit(main())
