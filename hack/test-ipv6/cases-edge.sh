#!/usr/bin/env bash

# Category 6: Edge Cases Test Cases

# TC-020: No Gateways Specified
tc020_no_gateways() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc020-no-gateways"
    
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
      addresses:
      - "192.168.1.100/24"
      - "2001:db8::100/64"
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

# TC-021: IPv6-Only with Multiple Addresses and Gateways
tc021_ipv6_only_multiple() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc021-ipv6-only-multiple"
    
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
      addresses:
      - "2001:db8::100/64"
      - "2001:db8::101/64"
      gateway6: "2001:db8::1"
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

# TC-022: User Override with Partial Gateway Backfill
tc022_partial_gateway_backfill() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc022-partial-gateway-backfill"
    
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
      addresses:
      - "192.168.1.100/24"
      - "2001:db8::100/64"
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

# TC-023: Dual-Stack with Different Subnet Masks
tc023_different_subnets() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc023-different-subnets"
    
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
      addresses:
      - "192.168.1.100/16"
      - "2001:db8::100/56"
      gateway4: "192.168.1.1"
      gateway6: "2001:db8::1"
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

