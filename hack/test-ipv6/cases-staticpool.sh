#!/usr/bin/env bash

# Category 1: NetOP StaticPool Mode Test Cases

# TC-001: IPv6-Only StaticPool from NetOP
tc001_ipv6_only() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"
    
    local vm_name="tc001-ipv6-only"
    
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

# TC-002: Dual-Stack StaticPool from NetOP
tc002_dual_stack() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"
    
    local vm_name="tc002-dual-stack"
    
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
    hostName: dual-stack-vm
    domainName: example.com
    nameservers:
    - "8.8.8.8"
    - "2001:4860:4860::8888"
    searchDomains:
    - "example.com"
    - "internal.example.com"
    interfaces:
    - name: eth0
      network:
        name: $primary_network
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

# TC-003: Dual-Stack with WaitOnNetwork
tc003_dual_stack_wait_network() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"
    
    local vm_name="tc003-dual-stack-wait-network"
    
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
    hostName: dual-stack-wait
    domainName: example.com
    nameservers:
    - "8.8.8.8"
    - "2001:4860:4860::8888"
    interfaces:
    - name: eth0
      network:
        name: $primary_network
  bootstrap:
    cloudInit:
      waitOnNetwork4: true
      waitOnNetwork6: true
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

# TC-004: Multiple IPv6 Addresses StaticPool from NetOP
tc004_multiple_ipv6() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"
    
    local vm_name="tc004-multiple-ipv6"
    
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

# TC-005: Dual-Stack with Multiple IPv6 StaticPool from NetOP
tc005_dual_stack_multi_ipv6() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"
    
    local vm_name="tc005-dual-stack-multi-ipv6"
    
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
    hostName: dual-stack-multi-ipv6
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

