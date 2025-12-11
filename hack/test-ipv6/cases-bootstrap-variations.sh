#!/usr/bin/env bash

# Category 5: Bootstrap Provider Variations Test Cases

# TC-014: GOSC IPv6-Only Static with IPv6-Only Fix
tc014_gosc_ipv6_only() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

    local vm_name="tc014-gosc-ipv6-only"

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
      gateway6: "2001:db8::1"
  bootstrap:
    linuxPrep:
      hostName: gosc-ipv6-vm
      domainName: example.com
      hardwareClockIsUTC: true
      timeZone: "America/Los_Angeles"
      password:
        name: linuxprep-password
        key: password
      expirePasswordAfterNextLogin: false
EOF
}

# TC-015: GOSC Dual-Stack Static Configuration
tc015_gosc_dual_stack() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

    local vm_name="tc015-gosc-dual-stack"

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
    hostName: gosc-dual-stack
    domainName: example.com
    nameservers:
    - "8.8.8.8"
    - "2001:4860:4860::8888"
    interfaces:
    - name: eth0
      network:
        name: $primary_network
      addresses:
      - "192.168.1.100/24"
      - "2001:db8::100/64"
      gateway4: "192.168.1.1"
      gateway6: "2001:db8::1"
  bootstrap:
    linuxPrep:
      hostName: gosc-dual-stack
      domainName: example.com
      hardwareClockIsUTC: true
      timeZone: "America/Los_Angeles"
      password:
        name: linuxprep-password
        key: password
      expirePasswordAfterNextLogin: false
EOF
}

# TC-016: GOSC DHCP4 + DHCP6 Configuration
tc016_gosc_dhcp4_dhcp6() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

    local vm_name="tc016-gosc-dhcp4-dhcp6"

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
    linuxPrep:
      hostName: gosc-dhcp
      domainName: example.com
      hardwareClockIsUTC: true
      timeZone: "America/Los_Angeles"
      password:
        name: linuxprep-password
        key: password
      expirePasswordAfterNextLogin: false
EOF
}

# TC-017: GOSC DHCP4 + Static IPv6 Configuration
tc017_gosc_dhcp4_static_ipv6() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

    local vm_name="tc017-gosc-dhcp4-static-ipv6"

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
      addresses:
      - "2001:db8::100/64"
      gateway6: "2001:db8::1"
  bootstrap:
    linuxPrep:
      hostName: gosc-dhcp4-ipv6
      domainName: example.com
      hardwareClockIsUTC: true
      timeZone: "America/Los_Angeles"
      password:
        name: linuxprep-password
        key: password
      expirePasswordAfterNextLogin: false
EOF
}

# TC-018: GOSC Static IPv4 + DHCP6 Configuration
tc018_gosc_static_ipv4_dhcp6() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

    local vm_name="tc018-gosc-static-ipv4-dhcp6"

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
      gateway4: "192.168.1.1"
      dhcp6: true
  bootstrap:
    linuxPrep:
      hostName: gosc-ipv4-dhcp6
      domainName: example.com
      hardwareClockIsUTC: true
      timeZone: "America/Los_Angeles"
      password:
        name: linuxprep-password
        key: password
      expirePasswordAfterNextLogin: false
EOF
}

# TC-019: Cloud-Init Dual-Stack with Multiple Addresses
tc019_cloudinit_multiple_addresses() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

    local vm_name="tc019-cloudinit-multiple-addresses"

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
    hostName: multi-addr-vm
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
      addresses:
      - "192.168.1.100/24"
      - "192.168.1.101/24"
      - "2001:db8::100/64"
      - "2001:db8::101/64"
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

