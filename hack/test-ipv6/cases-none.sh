#!/usr/bin/env bash

# Category 3: NetOP None Mode Test Cases

# TC-006: NoIPAM Mode from NetOP
tc006_noipam() {
    local namespace="$1"
    local primary_network="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"
    
    local vm_name="tc006-noipam"
    
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

