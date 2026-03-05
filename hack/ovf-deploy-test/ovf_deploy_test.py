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
import socket
import ssl
import sys
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
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
OVF_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ovf_cache.json")


# Timeouts
VMI_WAIT_TIMEOUT = 300  # 5 minutes
VM_TOOLS_WAIT_TIMEOUT = 600  # 10 minutes
POLL_INTERVAL = 10  # seconds

# OVF XML namespaces
OVF_NS = "http://schemas.dmtf.org/ovf/envelope/1"
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

    # Get VM name
    name_el = root.find(f".//{{{OVF_NS}}}VirtualSystem/{{{OVF_NS}}}Name")
    name = name_el.text if name_el is not None else "unknown"

    info = OvfInfo(name=name)

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

    def find_library_item(self, library_id: str, item_name: str) -> Optional[str]:
        """Find a library item by name in a library."""
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
                return item_id

        return None

    def upload_ovf(self, library_id: str, source: str, item_name: str) -> str:
        """
        Upload an OVF/OVA to the content library from a URL or local file path.
        """
        print(f"Uploading OVF '{item_name}' from {source} to content library...")

        # Check if item already exists
        existing_item = self.find_library_item(library_id, item_name)
        if existing_item:
            print(f"  Item '{item_name}' already exists in library, skipping upload")
            return existing_item

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

            # Complete the session
            complete_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}?action=complete"
            response = self.rest_session.post(complete_url)
            if not response.ok:
                raise RuntimeError(
                    f"Failed to complete update session: "
                    f"{response.status_code} {response.reason}\n{response.text}"
                )
            print("  Upload completed successfully")

        except Exception as e:
            # Cancel session on failure
            cancel_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}?action=cancel"
            self.rest_session.post(cancel_url)
            raise e

        return item_id

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
                with open(source, 'rb') as f:
                    self._upload_file_content(session_id, filename, f.read())
            elif filename.endswith('.ovf'):
                base_dir = os.path.dirname(source)
                with open(source) as f:
                    ovf_content = f.read()
                self._upload_file_content(session_id, filename, ovf_content.encode())
                vmdk_refs = re.findall(r'ovf:href="([^"]+\.vmdk)"', ovf_content)
                for vmdk_ref in vmdk_refs:
                    vmdk_path = os.path.join(base_dir, vmdk_ref)
                    print(f"  Uploading local VMDK {vmdk_ref} ({os.path.getsize(vmdk_path)} bytes)...")
                    with open(vmdk_path, 'rb') as f:
                        self._upload_file_content(session_id, vmdk_ref, f.read())
            else:
                raise ValueError(f"Unsupported file type: {filename}")
        else:
            # Remote URL - use PULL with SSL cert
            ssl_cert = self._get_ssl_certificate(source)

            if filename.endswith('.ova'):
                self._upload_file_from_url(session_id, source, filename, ssl_cert)
            elif filename.endswith('.ovf'):
                base_url = source.rsplit('/', 1)[0] + '/'
                print(f"  Downloading OVF descriptor from {source}...")
                response = requests.get(source, verify=False, timeout=60)
                response.raise_for_status()
                ovf_content = response.text
                self._upload_file_content(session_id, filename, ovf_content.encode())
                vmdk_refs = re.findall(r'ovf:href="([^"]+\.vmdk)"', ovf_content)
                for vmdk_ref in vmdk_refs:
                    vmdk_url = urljoin(base_url, vmdk_ref)
                    print(f"  Adding VMDK for transfer: {vmdk_ref}")
                    self._upload_file_from_url(session_id, vmdk_url, vmdk_ref, ssl_cert)
            else:
                raise ValueError(f"Unsupported file type: {filename}")

    def _get_ssl_certificate(self, url: str) -> Optional[str]:
        """
        Get the SSL certificate from a remote server in PEM format.

        This certificate can be passed to the Content Library API so vCenter
        trusts the remote server when pulling files.
        """
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or 443

        print(f"  Fetching SSL certificate from {hostname}:{port}...")
        try:
            # Connect and get the certificate
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            with socket.create_connection((hostname, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert_der = ssock.getpeercert(binary_form=True)

            # Convert DER to PEM format
            cert_pem = ssl.DER_cert_to_PEM_cert(cert_der)
            print(f"  Got SSL certificate for {hostname}")
            return cert_pem

        except Exception as e:
            print(f"  Warning: Could not get SSL certificate: {e}")
            return None

    def _upload_file_from_url(self, session_id: str, file_url: str, filename: str,
                               ssl_cert: Optional[str] = None) -> None:
        """
        Upload a file from URL to the update session using PULL method.

        Args:
            session_id: Content Library update session ID
            file_url: URL to download the file from
            filename: Name for the file in the library
            ssl_cert: PEM-encoded SSL certificate for vCenter to trust the remote server
        """
        add_spec = {
            "name": filename,
            "source_type": "PULL",
            "source_endpoint": {
                "uri": file_url
            }
        }

        # Add SSL certificate if provided
        if ssl_cert:
            add_spec["source_endpoint"]["ssl_certificate"] = ssl_cert

        add_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}/file"
        response = self.rest_session.post(add_url, json=add_spec)
        if not response.ok:
            raise RuntimeError(
                f"Failed to add file {filename} for PULL: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        print(f"  Added file {filename} (PULL from URL with SSL cert)")

    def _upload_file_content(self, session_id: str, filename: str, content: bytes) -> None:
        """Upload file content directly to the update session."""
        # Add file to session with PUSH source type
        add_spec = {
            "name": filename,
            "source_type": "PUSH",
            "size": len(content)
        }

        add_url = f"https://{self.host}/api/content/library/item/update-session/{session_id}/file"
        response = self.rest_session.post(add_url, json=add_spec)
        if not response.ok:
            raise RuntimeError(
                f"Failed to add file {filename} for PUSH: "
                f"{response.status_code} {response.reason}\n{response.text}"
            )
        file_info = response.json()

        # Upload the content
        upload_uri = file_info.get("upload_endpoint", {}).get("uri")
        if upload_uri:
            upload_response = self.rest_session.put(
                upload_uri,
                data=content,
                headers={"Content-Type": "application/octet-stream"}
            )
            if not upload_response.ok:
                raise RuntimeError(
                    f"Failed to upload content for {filename}: "
                    f"{upload_response.status_code} {upload_response.reason}\n{upload_response.text}"
                )
        print(f"  Uploaded file {filename} ({len(content)} bytes)")


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

    def wait_for_vmi(self, namespace: str, image_name: str, timeout: int = VMI_WAIT_TIMEOUT) -> Optional[str]:
        """Wait for a VirtualMachineImage to be ready."""
        print(f"Waiting for VirtualMachineImage containing '{image_name}'...")
        start_time = time.time()

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
                        # Match by display name or metadata name
                        if image_name.lower() in vmi_name.lower() or image_name.lower() in display_name.lower():
                            # Check if ready
                            conditions = item.get("status", {}).get("conditions", [])
                            for cond in conditions:
                                if cond.get("type") == "Ready" and cond.get("status") == "True":
                                    print(f"  Found ready VMI: {vmi_name}")
                                    return vmi_name
                except json.JSONDecodeError:
                    pass

            print(f"  Waiting... ({int(time.time() - start_time)}s)")
            time.sleep(POLL_INTERVAL)

        return None

    def create_vm(self, namespace: str, vm_name: str, image_name: str,
                  vm_class: str, storage_class: str,
                  ovf_info: Optional[OvfInfo] = None,
                  vapp_config: Optional[list] = None) -> None:
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

        # Add network interfaces from OVF network definitions
        if ovf_info and ovf_info.has_networks():
            interfaces = []
            for i, net in enumerate(ovf_info.networks):
                interfaces.append({
                    "name": f"eth{i}",
                    "network": {
                        "apiVersion": "crd.nsx.vmware.com/v1alpha1",
                        "kind": "SubnetSet",
                        "name": ""
                    }
                })
            if interfaces:
                spec["network"] = {"interfaces": interfaces}

        # vAppConfig: use provided config directly, or fall back to OVF defaults
        if vapp_config:
            spec["bootstrap"] = {
                "vAppConfig": {"properties": vapp_config}
            }
        elif ovf_info and ovf_info.has_properties():
            props = [
                {"key": p.key, "value": {"value": p.default or ""}}
                for p in ovf_info.properties
            ]
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

    def wait_for_vm_tools(self, namespace: str, vm_name: str, timeout: int = VM_TOOLS_WAIT_TIMEOUT) -> tuple[bool, dict]:
        """
        Wait for VM tools to be running inside the VM.

        Returns (tools_running, vm_status_dict) where vm_status_dict is the
        last observed .status from the VM CR.
        """
        print(f"Waiting for VM tools to run in {vm_name}...")
        start_time = time.time()
        last_status: dict = {}

        while time.time() - start_time < timeout:
            stdout, _, _ = self.run_kubectl(
                f"get vm -n {namespace} {vm_name} -o json",
                check=False
            )
            if stdout:
                try:
                    vm = json.loads(stdout)
                    last_status = vm.get("status", {})
                    vm_tools = last_status.get("vmwareTools", {})
                    tools_status = vm_tools.get("runningStatus", "")
                    if tools_status == "guestToolsRunning":
                        print(f"  VM tools are running")
                        return True, last_status
                    phase = last_status.get("phase", "Unknown")
                    print(f"  Waiting... ({int(time.time() - start_time)}s) - Phase: {phase}, Tools: {tools_status or 'unknown'}")
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

    def get_vmop_logs_for_vm(self, vm_name: str, lines: int = 200) -> str:
        """
        Fetch recent vmop controller-manager logs filtered to the given VM name.

        Returns relevant log lines as a single string, empty if none found.
        """
        stdout, _, _ = self.run_kubectl(
            f"logs deploy/vmware-system-vmop-controller-manager "
            f"-n vmware-system-vmop --tail={lines} 2>/dev/null | grep {vm_name} || true",
            check=False
        )
        return stdout.strip() if stdout else ""

    def delete_vm(self, namespace: str, vm_name: str) -> None:
        """Delete a VirtualMachine."""
        self.run_kubectl(f"delete vm -n {namespace} {vm_name} --ignore-not-found")
        print(f"  Deleted VM {vm_name}")


def discover_ovfs(base_url: str, refresh: bool = False) -> list[str]:
    """
    Discover OVF/OVA files from a directory listing URL.

    Supports JFrog Artifactory repositories by using the storage API
    to recursively browse subdirectories. Results are cached in ovf_cache.json
    next to the script; pass refresh=True to force a fresh discovery.

    Args:
        base_url: Base URL to discover OVFs from
        refresh: If True, ignore the cache and re-discover

    Returns:
        List of OVF/OVA URLs
    """
    if not refresh and os.path.exists(OVF_CACHE_FILE):
        try:
            with open(OVF_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            if cache.get("base_url") == base_url:
                ovfs = cache.get("ovfs", [])
                print(f"Using cached OVF list ({len(ovfs)} files) from {OVF_CACHE_FILE}")
                return ovfs
        except Exception:
            pass  # Cache unreadable, fall through to discovery

    print(f"Discovering OVFs from {base_url}...")

    # Convert UI URL to Artifactory API URL if needed
    # https://packages.vcfd.broadcom.net/ui/native/cls-generic-virtual/testdata/
    # -> https://packages.vcfd.broadcom.net/artifactory/api/storage/cls-generic-virtual/testdata/
    api_url = base_url
    if "/ui/native/" in base_url:
        api_url = base_url.replace("/ui/native/", "/artifactory/api/storage/")
    elif "/artifactory/" in base_url and "/api/storage/" not in base_url:
        # Direct artifactory path like /artifactory/cls-generic-virtual/testdata/
        api_url = base_url.replace("/artifactory/", "/artifactory/api/storage/")

    # Also compute the download base URL
    download_base = base_url
    if "/ui/native/" in base_url:
        download_base = base_url.replace("/ui/native/", "/artifactory/")
    elif "/api/storage/" in base_url:
        download_base = base_url.replace("/api/storage/", "/")

    ovfs = []

    def browse_directory(api_path: str, download_path: str, depth: int = 0) -> None:
        """Recursively browse directory for OVF/OVA files."""
        if depth > 3:  # Limit recursion depth
            return

        try:
            response = requests.get(api_path, verify=False, timeout=30)
            response.raise_for_status()
            data = response.json()

            for child in data.get("children", []):
                child_uri = child.get("uri", "").lstrip("/")
                is_folder = child.get("folder", False)

                if is_folder:
                    # Recurse into subdirectory
                    child_api = api_path.rstrip("/") + "/" + child_uri
                    child_download = download_path.rstrip("/") + "/" + child_uri
                    browse_directory(child_api, child_download, depth + 1)
                else:
                    # Check if it's an OVF or OVA file
                    if child_uri.lower().endswith(('.ovf', '.ova')):
                        full_url = download_path.rstrip("/") + "/" + child_uri
                        ovfs.append(full_url)

        except Exception as e:
            print(f"  Warning: Could not browse {api_path}: {e}")

    try:
        browse_directory(api_url, download_base)

        print(f"  Found {len(ovfs)} OVF/OVA files")
        for ovf in ovfs[:10]:  # Show first 10
            print(f"    - {ovf}")
        if len(ovfs) > 10:
            print(f"    ... and {len(ovfs) - 10} more")

        try:
            with open(OVF_CACHE_FILE, 'w') as f:
                json.dump({"base_url": base_url, "ovfs": ovfs}, f, indent=2)
            print(f"  Cached OVF list to {OVF_CACHE_FILE}")
        except Exception as e:
            print(f"  Warning: Could not write OVF cache: {e}")

        return ovfs

    except Exception as e:
        print(f"  Warning: Could not discover OVFs: {e}")
        return []


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
    """Convert a list of discovered OVF/OVA URLs into OvfEntry objects."""
    entries = []
    for url in urls:
        filename = os.path.basename(urlparse(url).path)
        item_name = os.path.splitext(filename)[0]
        entries.append(OvfEntry(name=item_name, source=url))
    return entries


def fetch_ovf_info(source: str) -> Optional[OvfInfo]:
    """Parse OVF info from a local file path or remote URL."""
    if os.path.exists(source):
        return fetch_ovf_from_file(source)
    return fetch_ovf_from_url(source)


def write_report(results: list[DeployResult], report_path: str) -> None:
    """Write a deployment results table to a file and print it to stdout."""
    col_name   = max(len("OVF Name"),    max((len(r.name)    for r in results), default=0))
    col_vm     = max(len("VM Name"),     max((len(r.vm_name) for r in results), default=0))
    col_status = max(len("Status"),      max((len(r.status)  for r in results), default=0))
    col_reason = max(len("Reason"),      max((len(r.reason)  for r in results), default=0))

    sep   = f"+{'-'*(col_name+2)}+{'-'*(col_vm+2)}+{'-'*(col_status+2)}+{'-'*(col_reason+2)}+"
    hdr   = f"| {'OVF Name':<{col_name}} | {'VM Name':<{col_vm}} | {'Status':<{col_status}} | {'Reason':<{col_reason}} |"

    lines = [sep, hdr, sep]
    for r in results:
        lines.append(
            f"| {r.name:<{col_name}} | {r.vm_name:<{col_vm}} | {r.status:<{col_status}} | {r.reason:<{col_reason}} |"
        )
    lines.append(sep)

    table = "\n".join(lines)
    print(f"\n{table}")

    with open(report_path, 'w') as f:
        f.write(f"OVF Deploy Test Report\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(table)
        f.write("\n")

        # Append vmop log excerpts for any non-SUCCESS entries
        failed = [r for r in results if r.status != "SUCCESS" and r.vmop_logs]
        if failed:
            f.write("\n\nVMOP Log Excerpts\n")
            f.write("=" * 60 + "\n")
            for r in failed:
                f.write(f"\n--- {r.vm_name} ---\n")
                f.write(r.vmop_logs)
                f.write("\n")

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
    urls = discover_ovfs(args.base_url, refresh=args.refresh_cache)
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
                    if ovf_info.has_networks():
                        print(f"    Networks: {[n.name for n in ovf_info.networks]}")
                    if ovf_info.has_properties():
                        print(f"    vApp properties: {len(ovf_info.properties)} keys")
                else:
                    print("    Warning: Could not parse OVF, proceeding without network/property info")

                vapp_config = None
                if entry.config_file:
                    vapp_config = load_vapp_config(entry.config_file)

                vcenter.upload_ovf(library_id, entry.source, item_name)

                vmi_name = supervisor.wait_for_vmi(args.namespace, item_name)
                if not vmi_name:
                    reason = supervisor.get_vm_status_reason(args.namespace, vm_name)
                    logs = supervisor.get_vmop_logs_for_vm(vm_name)
                    results.append(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="FAILED", reason=f"VMI not found. {reason}", vmop_logs=logs
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

                supervisor.create_vm(
                    namespace=args.namespace,
                    vm_name=vm_name,
                    image_name=vmi_name,
                    vm_class=args.vm_class,
                    storage_class=STORAGE_CLASS,
                    ovf_info=ovf_info,
                    vapp_config=vapp_config,
                )

                tools_running, _ = supervisor.wait_for_vm_tools(args.namespace, vm_name)

                if tools_running:
                    results.append(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="SUCCESS", reason="VM tools running"
                    ))
                else:
                    reason = supervisor.get_vm_status_reason(args.namespace, vm_name)
                    logs = supervisor.get_vmop_logs_for_vm(vm_name)
                    results.append(DeployResult(
                        name=entry.name, source=entry.source, vm_name=vm_name,
                        status="PARTIAL", reason=f"Tools not running within timeout. {reason}",
                        vmop_logs=logs
                    ))

                if args.cleanup:
                    supervisor.delete_vm(args.namespace, vm_name)

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
                results.append(DeployResult(
                    name=entry.name, source=entry.source, vm_name=vm_name,
                    status="FAILED", reason=reason, vmop_logs=logs
                ))

        report_path = args.report or (os.path.splitext(args.csv)[0] + ".report.txt")
        write_report(results, report_path)

        return 1 if any(r.status == "FAILED" for r in results) else 0

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
    p_discover.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore cached OVF list and re-discover"
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
        "--cleanup",
        action="store_true",
        help="Delete each VM after it is verified"
    )
    p_deploy.add_argument(
        "--report",
        help="Path to write the results report (default: <csv>.report.txt)"
    )

    args = parser.parse_args()

    if args.command == "discover":
        return cmd_discover(args)
    else:
        return cmd_deploy(args)


if __name__ == "__main__":
    sys.exit(main())
