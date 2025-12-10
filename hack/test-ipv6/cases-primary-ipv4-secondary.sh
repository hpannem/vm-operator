#!/usr/bin/env bash

# Category 7: Primary IPv4 + Secondary Network Test Cases

# TC-024: Primary IPv4 + Secondary IPv6 StaticPool
tc024_primary_ipv4_secondary_ipv6() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"

    local vm_name="tc024-primary-ipv4-secondary-ipv6"

    cat <<EOF
apiVersion: vmoperator.vmware.com/v1alpha5
kind: VirtualMachine
metadata:
  name: $vm_name
  namespace: $namespace
spec:
  className: $vm_class
  imageName: $vmi_id
  storageClass: $storage_class
  powerState: PoweredOn
  network:
    interfaces:
    - name: eth0
      network:
        name: $primary_network
    - name: eth1
      network:
        name: $secondary_network
  bootstrap:
    cloudInit:
      cloudConfig:
        users:
        - name: test
          primary_group: test
          sudo: ALL=(ALL) NOPASSWD:ALL
          lock_passwd: false
          groups: ["users"]
          passwd:
            name: vm-cloud-init-bootstrap-data
            key: test-passwd
EOF
}

# TC-025: Primary IPv4 + Secondary Dual-Stack StaticPool
tc025_primary_ipv4_secondary_dual_stack() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"

    local vm_name="tc025-primary-ipv4-secondary-dual-stack"

    cat <<EOF
apiVersion: vmoperator.vmware.com/v1alpha5
kind: VirtualMachine
metadata:
  name: $vm_name
  namespace: $namespace
spec:
  className: $vm_class
  imageName: $vmi_id
  storageClass: $storage_class
  powerState: PoweredOn
  network:
    hostName: primary-ipv4-secondary-dual-stack
    domainName: example.com
    nameservers:
    - "8.8.8.8"
    - "2001:4860:4860::8888"
    searchDomains:
    - "example.com"
    interfaces:
    - name: eth0
      network:
        name: $primary_network
    - name: eth1
      network:
        name: $secondary_network
  bootstrap:
    cloudInit:
      cloudConfig:
        users:
        - name: test
          primary_group: test
          sudo: ALL=(ALL) NOPASSWD:ALL
          lock_passwd: false
          groups: ["users"]
          passwd:
            name: vm-cloud-init-bootstrap-data
            key: test-passwd
EOF
}

# TC-026: Primary IPv4 + Secondary NoIPAM
tc026_primary_ipv4_secondary_noipam() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"

    local vm_name="tc026-primary-ipv4-secondary-noipam"

    cat <<EOF
apiVersion: vmoperator.vmware.com/v1alpha5
kind: VirtualMachine
metadata:
  name: $vm_name
  namespace: $namespace
spec:
  className: $vm_class
  imageName: $vmi_id
  storageClass: $storage_class
  powerState: PoweredOn
  network:
    interfaces:
    - name: eth0
      network:
        name: $primary_network
    - name: eth1
      network:
        name: $secondary_network
  bootstrap:
    cloudInit:
      cloudConfig:
        users:
        - name: test
          primary_group: test
          sudo: ALL=(ALL) NOPASSWD:ALL
          lock_passwd: false
          groups: ["users"]
          passwd:
            name: vm-cloud-init-bootstrap-data
            key: test-passwd
EOF
}

# TC-027: Primary IPv4 + Secondary DHCP
tc027_primary_ipv4_secondary_dhcp() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"

    local vm_name="tc027-primary-ipv4-secondary-dhcp"

    cat <<EOF
apiVersion: vmoperator.vmware.com/v1alpha5
kind: VirtualMachine
metadata:
  name: $vm_name
  namespace: $namespace
spec:
  className: $vm_class
  imageName: $vmi_id
  storageClass: $storage_class
  powerState: PoweredOn
  network:
    hostName: primary-ipv4-secondary-dhcp
    domainName: example.com
    nameservers:
    - "8.8.8.8"
    - "2001:4860:4860::8888"
    searchDomains:
    - "example.com"
    interfaces:
    - name: eth0
      network:
        name: $primary_network
    - name: eth1
      network:
        name: $secondary_network
  bootstrap:
    cloudInit:
      cloudConfig:
        users:
        - name: test
          primary_group: test
          sudo: ALL=(ALL) NOPASSWD:ALL
          lock_passwd: false
          groups: ["users"]
          passwd:
            name: vm-cloud-init-bootstrap-data
            key: test-passwd
EOF
}

