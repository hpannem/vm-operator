#!/usr/bin/env bash

# Category 4: User-Specified Overrides Test Cases

# TC-007: User Addresses Override NetOP StaticPool
tc007_user_addresses_override() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc007-user-addresses-override"
    
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

# TC-008: User Gateways Override NetOP Gateways
tc008_user_gateways_override() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc008-user-gateways-override"
    
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
      gateway4: "172.16.1.1"
      gateway6: "2001:db8::2"
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

# TC-009: User Gateways "None" Clears NetOP Gateways
tc009_gateway_none() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc009-gateway-none"
    
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
      gateway4: "None"
      gateway6: "None"
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

# TC-010: User DHCP6 Overrides NetOP StaticPool
tc010_dhcp6_override() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc010-dhcp6-override"
    
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
      dhcp6: true
      addresses:
      - "192.168.1.100/24"
      gateway4: "192.168.1.1"
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

# TC-011: User DHCP4 + DHCP6 Override NetOP StaticPool
tc011_dhcp4_dhcp6_override() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc011-dhcp4-dhcp6-override"
    
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
      dhcp4: true
      dhcp6: true
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

# TC-012: User Addresses + NetOP Gateways (Gateway Backfill)
tc012_gateway_backfill() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc012-gateway-backfill"
    
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
      - "10.0.0.100/24"
      - "2001:db8::200/64"
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

# TC-013: NetOP Addresses + User Gateways
tc013_netop_addresses_user_gateways() {
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    
    local vm_name="tc013-netop-addresses-user-gateways"
    
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
      gateway4: "172.16.1.1"
      gateway6: "2001:db8::2"
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

