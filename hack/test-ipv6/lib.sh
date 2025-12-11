#!/usr/bin/env bash

# Helper function library for IPv6 integration tests

# VM Condition constant (only set if not already set to avoid readonly error on re-source)
if [[ -z "${VM_NETWORK_CONDITION:-}" ]]; then
    readonly VM_NETWORK_CONDITION="VirtualMachineGuestNetworkConfigSynced"
fi

# Colors for output (if not already defined)
if [[ -z "${RED:-}" ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    NC='\033[0m' # No Color
fi

# Initialize kubectl context
init_kubectl() {
    local kubeconfig="$1"
    export KUBECONFIG="$kubeconfig"

    # Verify kubectl is available
    if ! command -v kubectl &> /dev/null; then
        echo "Error: kubectl not found in PATH"
        exit 1
    fi

    # Verify jq is available
    if ! command -v jq &> /dev/null; then
        echo "Error: jq not found in PATH (required for JSON parsing)"
        exit 1
    fi

    # Verify cluster connectivity
    if ! kubectl cluster-info &>/dev/null; then
        echo "Error: Cannot connect to cluster"
        exit 1
    fi
}

# Discover VMI in namespace or cluster-scoped
discover_vmi() {
    local namespace="$1"

    # First try namespace-scoped VMI
    local vmi=$(kubectl get vmi -n "$namespace" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -n "$vmi" ]]; then
        echo "$vmi"
        return 0
    fi

    # Try cluster-scoped CVMI
    local cvmi=$(kubectl get cvmi -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -n "$cvmi" ]]; then
        echo "$cvmi"
        return 0
    fi

    return 1
}

# Create Cloud-Init and LinuxPrep password secrets
create_secrets() {
    local namespace="$1"

    # Cloud-Init password secret
    local cloudinit_secret="vm-cloud-init-bootstrap-data"
    if ! kubectl get secret "$cloudinit_secret" -n "$namespace" &>/dev/null; then
        kubectl create secret generic "$cloudinit_secret" \
            --from-literal=test-passwd="VMware123!" \
            --from-literal=default-passwd='$6$6kcg7S6ZkIfF0pEx$j4V6pLE4MjcgxlnaR/.QC9UocCI9wZm.YOeJA6E6xjR4grHxV6WCWp7.vBlUZnL530uJGeIbYNf0Mr4DxofWO.' \
            -n "$namespace" &>/dev/null || true
    fi

    # LinuxPrep password secret
    local linuxprep_secret="linuxprep-password"
    if ! kubectl get secret "$linuxprep_secret" -n "$namespace" &>/dev/null; then
        kubectl create secret generic "$linuxprep_secret" \
            --from-literal=password="VMware123!" \
            -n "$namespace" &>/dev/null || true
    fi
}

# Generate base VM spec template
generate_vm_spec_base() {
    local vm_name="$1"
    local namespace="$2"
    local vmi_id="$3"
    local vm_class="$4"
    local storage_class="$5"

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
EOF
}

# Check VM Network Config Synced condition
verify_vm_ready() {
    local vm_name="$1"
    local namespace="$2"

    local ready_status=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.conditions[?(@.type==\"${VM_NETWORK_CONDITION}\")].status}" 2>/dev/null)
    [[ "$ready_status" == "True" ]]
}

# Get VM IP addresses from status with retry logic
# If interface_name is provided, returns IPs for that specific interface only
get_vm_ip_addresses() {
    local vm_name="$1"
    local namespace="$2"
    local max_retries="${3:-10}"  # Default 10 retries
    local retry_delay="${4:-3}"    # Default 3 seconds between retries
    local quiet="${5:-false}"      # If true, don't print retry messages
    local interface_name="${6:-}"  # Optional: specific interface name (e.g., eth0, eth1)

    local retry_count=0
    local ip_addresses=""
    local jsonpath=""

    # Build jsonpath based on whether we want a specific interface or all
    # Use printf to properly escape quotes for kubectl jsonpath
    if [[ -n "$interface_name" ]]; then
        printf -v jsonpath '{.status.network.interfaces[?(@.name=="%s")].ip.addresses[*].address}' "$interface_name"
    else
        jsonpath='{.status.network.interfaces[*].ip.addresses[*].address}'
    fi

    while [[ $retry_count -lt $max_retries ]]; do
        # Filter out link-local IPv6 addresses (fe80::/10) as they're not useful for verification
        ip_addresses=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="$jsonpath" 2>/dev/null | tr ' ' '\n' | grep -v '^$' | grep -v '^fe80::' | grep -v '^FE80::')

        if [[ -n "$ip_addresses" ]]; then
            echo "$ip_addresses"
            return 0
        fi

        ((retry_count++))
        if [[ $retry_count -lt $max_retries ]]; then
            if [[ "$quiet" != "true" ]]; then
                local interface_msg=""
                # Only add interface name if it's a valid interface name (starts with eth)
                if [[ -n "$interface_name" ]] && [[ "$interface_name" =~ ^eth[0-9]+ ]]; then
                    interface_msg=" for $interface_name"
                fi
                echo "Waiting for IP addresses$interface_msg... (retry $retry_count/$max_retries)" >&2
            fi
            sleep "$retry_delay"
        fi
    done

    # Return empty if no IPs found after all retries
    echo ""
    return 0
}

# Get NetworkInterface CR status
get_networkinterface_status() {
    local vm_name="$1"
    local namespace="$2"

    # Find NetworkInterface CR for this VM
    local netif_name=$(kubectl get networkinterface -n "$namespace" -l vmoperator.vmware.com/vm-name="$vm_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -z "$netif_name" ]]; then
        return 1
    fi

    # Get IPAssignmentMode
    local ip_mode=$(kubectl get networkinterface "$netif_name" -n "$namespace" -o jsonpath='{.status.IPAssignmentMode}' 2>/dev/null)

    # Get IPConfigs
    local ipconfigs=$(kubectl get networkinterface "$netif_name" -n "$namespace" -o jsonpath='{.status.IPConfigs[*]}' 2>/dev/null)

    echo "$ip_mode|$ipconfigs"
}

# Get NetworkInterface IPAssignmentMode
get_networkinterface_ipassignment_mode() {
    local vm_name="$1"
    local namespace="$2"

    local netif_name=$(kubectl get networkinterface -n "$namespace" -l vmoperator.vmware.com/vm-name="$vm_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -z "$netif_name" ]]; then
        return 1
    fi

    kubectl get networkinterface "$netif_name" -n "$namespace" -o jsonpath='{.status.IPAssignmentMode}' 2>/dev/null
}

# Get NetworkInterface IPConfigs as JSON
get_networkinterface_ipconfigs() {
    local vm_name="$1"
    local namespace="$2"

    local netif_name=$(kubectl get networkinterface -n "$namespace" -l vmoperator.vmware.com/vm-name="$vm_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -z "$netif_name" ]]; then
        return 1
    fi

    kubectl get networkinterface "$netif_name" -n "$namespace" -o jsonpath='{.status.IPConfigs}' 2>/dev/null
}

# Get VM DHCP status from VM status
get_vm_dhcp_status() {
    local vm_name="$1"
    local namespace="$2"
    local interface_name="${3:-eth0}"

    # Get DHCP4 and DHCP6 enabled status
    local dhcp4_enabled=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.network.config.interfaces[?(@.name==\"$interface_name\")].ip.dhcp.ip4.enabled}" 2>/dev/null)
    local dhcp6_enabled=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.network.config.interfaces[?(@.name==\"$interface_name\")].ip.dhcp.ip6.enabled}" 2>/dev/null)

    # Return as "dhcp4:true/false,dhcp6:true/false"
    echo "dhcp4:${dhcp4_enabled:-false},dhcp6:${dhcp6_enabled:-false}"
}

# Get VM spec DHCP flags
get_vm_spec_dhcp_flags() {
    local vm_name="$1"
    local namespace="$2"
    local interface_name="${3:-eth0}"

    local dhcp4=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.spec.network.interfaces[?(@.name==\"$interface_name\")].dhcp4}" 2>/dev/null)
    local dhcp6=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.spec.network.interfaces[?(@.name==\"$interface_name\")].dhcp6}" 2>/dev/null)

    echo "dhcp4:${dhcp4:-false},dhcp6:${dhcp6:-false}"
}

# Verify IP families (IPv6-only or dual-stack)
verify_ip_families() {
    local ip_addresses="$1"
    local expected_family="$2"

    local has_ipv4=false
    local has_ipv6=false

    while IFS= read -r ip; do
        # Skip empty lines
        [[ -z "$ip" ]] && continue
        # Skip link-local IPv6 addresses (fe80::/10) - they're not useful for verification
        [[ "$ip" =~ ^fe80:: ]] && continue
        [[ "$ip" =~ ^FE80:: ]] && continue

        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]]; then
            has_ipv4=true
        elif [[ "$ip" =~ : ]]; then
            has_ipv6=true
        fi
    done <<< "$ip_addresses"

    case "$expected_family" in
        "IPv4-only")
            [[ "$has_ipv4" == "true" ]] && [[ "$has_ipv6" == "false" ]]
            ;;
        "IPv6-only")
            [[ "$has_ipv6" == "true" ]] && [[ "$has_ipv4" == "false" ]]
            ;;
        "Dual-stack")
            [[ "$has_ipv6" == "true" ]] && [[ "$has_ipv4" == "true" ]]
            ;;
        "N/A")
            true  # NoIPAM mode - no IP addresses expected
            ;;
        *)
            return 1
            ;;
    esac
}

# Collect VM diagnostics
collect_vm_diagnostics() {
    local vm_name="$1"
    local namespace="$2"
    local output=""

    # VM Network Config Synced status
    local ready_status=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.conditions[?(@.type==\"${VM_NETWORK_CONDITION}\")].status}" 2>/dev/null || echo "Unknown")
    local ready_reason=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.conditions[?(@.type==\"${VM_NETWORK_CONDITION}\")].reason}" 2>/dev/null || echo "")
    local ready_message=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.conditions[?(@.type==\"${VM_NETWORK_CONDITION}\")].message}" 2>/dev/null || echo "")

    output+="VM Network Config Synced Status: $ready_status"
    if [[ -n "$ready_reason" ]]; then
        output+=" (Reason: $ready_reason)"
    fi
    if [[ -n "$ready_message" ]]; then
        output+=" (Message: $ready_message)"
    fi
    output+="\n"

    # IP addresses (with retry, quiet mode for diagnostics)
    local ip_addresses=$(get_vm_ip_addresses "$vm_name" "$namespace" 5 2 true | tr '\n' ',' | sed 's/,$//')
    if [[ -n "$ip_addresses" ]]; then
        output+="IP Addresses: $ip_addresses\n"
    else
        output+="IP Addresses: None assigned\n"
    fi

    echo -e "$output"
}

# Collect NetworkInterface diagnostics
collect_networkinterface_diagnostics() {
    local vm_name="$1"
    local namespace="$2"
    local output=""

    local netif_name=$(kubectl get networkinterface -n "$namespace" -l vmoperator.vmware.com/vm-name="$vm_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [[ -z "$netif_name" ]]; then
        output+="NetworkInterface CR: Not found\n"
        echo -e "$output"
        return
    fi

    output+="NetworkInterface CR: $netif_name\n"

    local ip_mode=$(get_networkinterface_ipassignment_mode "$vm_name" "$namespace" 2>/dev/null || echo "Unknown")
    output+="IPAssignmentMode: $ip_mode\n"

    local ipconfigs_json=$(get_networkinterface_ipconfigs "$vm_name" "$namespace" 2>/dev/null || echo "[]")
    if [[ "$ipconfigs_json" != "[]" ]] && [[ -n "$ipconfigs_json" ]]; then
        output+="IPConfigs: $ipconfigs_json\n"
    else
        output+="IPConfigs: None\n"
    fi

    echo -e "$output"
}

# Collect DHCP diagnostics
collect_dhcp_diagnostics() {
    local vm_name="$1"
    local namespace="$2"
    local expected_dhcp_config="$3"
    local output=""

    if [[ "$expected_dhcp_config" == "none" ]]; then
        echo -e "$output"
        return
    fi

    local dhcp_status=$(get_vm_dhcp_status "$vm_name" "$namespace" "eth0")
    local dhcp4_enabled=$(echo "$dhcp_status" | grep -o "dhcp4:[^,]*" | cut -d: -f2)
    local dhcp6_enabled=$(echo "$dhcp_status" | grep -o "dhcp6:[^,]*" | cut -d: -f2)

    local spec_dhcp=$(get_vm_spec_dhcp_flags "$vm_name" "$namespace" "eth0")
    local spec_dhcp4=$(echo "$spec_dhcp" | grep -o "dhcp4:[^,]*" | cut -d: -f2)
    local spec_dhcp6=$(echo "$spec_dhcp" | grep -o "dhcp6:[^,]*" | cut -d: -f2)

    output+="DHCP Configuration:\n"
    output+="  Expected: $expected_dhcp_config\n"
    output+="  Spec (dhcp4/dhcp6): $spec_dhcp4/$spec_dhcp6\n"
    output+="  Status (dhcp4/dhcp6): $dhcp4_enabled/$dhcp6_enabled\n"

    echo -e "$output"
}

# Collect full diagnostics
collect_full_diagnostics() {
    local vm_name="$1"
    local namespace="$2"
    local expected_ip_family="$3"
    local expected_dhcp_config="$4"
    local output=""

    output+="VM Diagnostics for: $vm_name\n"
    output+="$(collect_vm_diagnostics "$vm_name" "$namespace")"
    output+="Expected IP Family: $expected_ip_family\n"

    # Determine actual IP family (with retry, quiet mode for diagnostics)
    local ip_addresses=$(get_vm_ip_addresses "$vm_name" "$namespace" 5 2 true)
    local has_ipv4=false
    local has_ipv6=false
    while IFS= read -r ip; do
        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]]; then
            has_ipv4=true
        elif [[ "$ip" =~ : ]]; then
            has_ipv6=true
        fi
    done <<< "$ip_addresses"

    local actual_family=""
    if [[ "$has_ipv4" == "true" ]] && [[ "$has_ipv6" == "true" ]]; then
        actual_family="Dual-stack"
    elif [[ "$has_ipv6" == "true" ]]; then
        actual_family="IPv6-only"
    elif [[ "$has_ipv4" == "true" ]]; then
        actual_family="IPv4-only"
    else
        actual_family="None"
    fi
    output+="Actual IP Family: $actual_family (IPv4=$has_ipv4, IPv6=$has_ipv6)\n"
    output+="\n"

    output+="$(collect_networkinterface_diagnostics "$vm_name" "$namespace")"
    output+="\n"
    output+="$(collect_dhcp_diagnostics "$vm_name" "$namespace" "$expected_dhcp_config")"

    echo -e "$output"
}

# Verify a specific interface has the expected IP family and address count
# expected_config format: "IP_FAMILY:COUNT" (e.g., "IPv6-only:2" or "Dual-stack:1")
# COUNT is optional, defaults to 1 if not specified
verify_interface_ip_family() {
    local vm_name="$1"
    local namespace="$2"
    local interface_name="$3"
    local expected_config="$4"  # Format: "IP_FAMILY:COUNT" or just "IP_FAMILY"
    local max_retries="${5:-10}"
    local retry_delay="${6:-3}"
    local quiet="${7:-false}"

    # Parse expected_config to extract IP family and count
    local expected_ip_family
    local expected_count=1
    if [[ "$expected_config" =~ : ]]; then
        # Format: "IP_FAMILY:COUNT"
        expected_ip_family="${expected_config%%:*}"
        expected_count="${expected_config##*:}"
        # Validate count is a number
        if ! [[ "$expected_count" =~ ^[0-9]+$ ]]; then
            echo "Invalid expected count: $expected_count (must be a number)"
            return 1
        fi
    else
        # Format: "IP_FAMILY" (default count is 1)
        expected_ip_family="$expected_config"
    fi

    # Get IP addresses for this interface
    # Parameters: vm_name, namespace, max_retries, retry_delay, quiet, interface_name
    local interface_ips
    interface_ips=$(get_vm_ip_addresses "$vm_name" "$namespace" "$max_retries" "$retry_delay" "$quiet" "$interface_name")

    # For N/A (NoIPAM), expect no IPs (count should be 0)
    if [[ "$expected_ip_family" == "N/A" ]]; then
        if [[ -n "$interface_ips" ]]; then
            echo "$interface_name expected no IP addresses (NoIPAM) but found: $(echo "$interface_ips" | tr '\n' ',' | sed 's/,$//')"
            return 1
        fi
        return 0
    fi

    if [[ -z "$interface_ips" ]]; then
        # Debug: Check if interface exists in VM status
        local interface_exists
        interface_exists=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.network.interfaces[?(@.name==\"$interface_name\")].name}" 2>/dev/null)
        if [[ -z "$interface_exists" ]]; then
            # List all available interface names for debugging
            local all_interfaces
            all_interfaces=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath='{.status.network.interfaces[*].name}' 2>/dev/null | tr ' ' '\n')
            echo "No IP addresses assigned to $interface_name after retries"
            if [[ -n "$all_interfaces" ]]; then
                echo "Available interfaces in VM status: $(echo "$all_interfaces" | tr '\n' ',' | sed 's/,$//')"
            else
                echo "No interfaces found in VM status"
            fi
        else
            echo "No IP addresses assigned to $interface_name after retries (interface exists but no IPs)"
        fi
        return 1
    fi

    # Verify IP family matches expected
    if ! verify_ip_families "$interface_ips" "$expected_ip_family"; then
        local has_ipv4=false
        local has_ipv6=false
        while IFS= read -r ip; do
            if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]]; then
                has_ipv4=true
            elif [[ "$ip" =~ : ]]; then
                has_ipv6=true
            fi
        done <<< "$interface_ips"

        echo "$interface_name IP family mismatch - Expected: $expected_ip_family, Got: IPv4=$has_ipv4, IPv6=$has_ipv6"
        if [[ -n "$interface_ips" ]]; then
            echo "$interface_name IPs: $(echo "$interface_ips" | tr '\n' ',' | sed 's/,$//')"
        fi
        return 1
    fi

    # Verify address count matches expected
    local ip_count=0
    while IFS= read -r ip; do
        [[ -n "$ip" ]] && ((ip_count++))
    done <<< "$interface_ips"

    if [[ $ip_count -ne $expected_count ]]; then
        echo "$interface_name address count mismatch - Expected: $expected_count, Got: $ip_count"
        if [[ -n "$interface_ips" ]]; then
            echo "$interface_name IPs: $(echo "$interface_ips" | tr '\n' ',' | sed 's/,$//')"
        fi
        return 1
    fi

    return 0
}

# Verify VM network configuration
# interface_expected_families: space-separated list of "interface:IP_FAMILY:COUNT" pairs
# Example: "eth0:IPv4-only:1 eth1:Dual-stack:2" or "eth0:IPv6-only:1" or "eth0:IPv6-only" (count defaults to 1)
verify_vm_network() {
    local vm_name="$1"
    local namespace="$2"
    local interface_expected_families="$3"  # Format: "eth0:IPv4-only:1 eth1:Dual-stack:2" or "eth0:IPv6-only"
    local expected_dhcp_config="${4:-none}"

    # Check VM Network Config Synced
    if ! verify_vm_ready "$vm_name" "$namespace"; then
        local ready_reason=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.conditions[?(@.type==\"${VM_NETWORK_CONDITION}\")].reason}" 2>/dev/null || echo "")
        local ready_message=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath="{.status.conditions[?(@.type==\"${VM_NETWORK_CONDITION}\")].message}" 2>/dev/null || echo "")
        echo "VM Network Config not Synced"
        if [[ -n "$ready_reason" ]]; then
            echo "Reason: $ready_reason"
        fi
        if [[ -n "$ready_message" ]]; then
            echo "Message: $ready_message"
        fi
        return 1
    fi

    # Verify each interface with its expected IP family and count
    local interface_config
    for interface_config in $interface_expected_families; do
        # Parse interface:family:count or interface:family (count defaults to 1)
        local interface_name
        local expected_config
        if [[ "$interface_config" =~ ^([^:]+):(.+)$ ]]; then
            interface_name="${BASH_REMATCH[1]}"
            expected_config="${BASH_REMATCH[2]}"
        else
            echo "Invalid interface config format: $interface_config (expected format: interface:family:count or interface:family)"
            return 1
        fi

        if ! verify_interface_ip_family "$vm_name" "$namespace" "$interface_name" "$expected_config" 10 3 false; then
            return 1
        fi
    done

    # Check NetworkInterface CR
    local netif_status
    if ! netif_status=$(get_networkinterface_status "$vm_name" "$namespace"); then
        echo "NetworkInterface CR not found - Searched for VM: $vm_name"
        return 1
    fi

    # Verify DHCP configuration if expected_dhcp_config is not "none"
    if [[ "$expected_dhcp_config" != "none" ]]; then
        local dhcp_status
        dhcp_status=$(get_vm_dhcp_status "$vm_name" "$namespace" "eth0")

        local dhcp4_enabled=$(echo "$dhcp_status" | grep -o "dhcp4:[^,]*" | cut -d: -f2)
        local dhcp6_enabled=$(echo "$dhcp_status" | grep -o "dhcp6:[^,]*" | cut -d: -f2)

        # Parse expected_dhcp_config
        local expect_dhcp4=false
        local expect_dhcp6=false
        local expect_static=false

        if [[ "$expected_dhcp_config" == *"dhcp4"* ]]; then
            expect_dhcp4=true
        fi
        if [[ "$expected_dhcp_config" == *"dhcp6"* ]]; then
            expect_dhcp6=true
        fi
        if [[ "$expected_dhcp_config" == *"static"* ]]; then
            expect_static=true
        fi

        # Get spec DHCP flags for better error messages
        local spec_dhcp=$(get_vm_spec_dhcp_flags "$vm_name" "$namespace" "eth0")
        local spec_dhcp4=$(echo "$spec_dhcp" | grep -o "dhcp4:[^,]*" | cut -d: -f2)
        local spec_dhcp6=$(echo "$spec_dhcp" | grep -o "dhcp6:[^,]*" | cut -d: -f2)

        # Verify DHCP4
        if [[ "$expect_dhcp4" == "true" ]] && [[ "$dhcp4_enabled" != "true" ]]; then
            echo "DHCP4 expected but not enabled - Spec: dhcp4=$spec_dhcp4, Status: enabled=$dhcp4_enabled"
            return 1
        fi
        if [[ "$expect_dhcp4" == "false" ]] && [[ "$dhcp4_enabled" == "true" ]]; then
            echo "DHCP4 not expected but enabled - Spec: dhcp4=$spec_dhcp4, Status: enabled=$dhcp4_enabled"
            return 1
        fi

        # Verify DHCP6
        if [[ "$expect_dhcp6" == "true" ]] && [[ "$dhcp6_enabled" != "true" ]]; then
            echo "DHCP6 expected but not enabled - Spec: dhcp6=$spec_dhcp6, Status: enabled=$dhcp6_enabled"
            return 1
        fi
        if [[ "$expect_dhcp6" == "false" ]] && [[ "$dhcp6_enabled" == "true" ]]; then
            echo "DHCP6 not expected but enabled - Spec: dhcp6=$spec_dhcp6, Status: enabled=$dhcp6_enabled"
            return 1
        fi

        # For pure DHCP scenarios (no static), verify IPAssignmentMode can be DHCP or StaticPool
        # For mixed scenarios, IPAssignmentMode should be StaticPool
        local ip_mode
        if ip_mode=$(get_networkinterface_ipassignment_mode "$vm_name" "$namespace"); then
            if [[ "$expect_static" == "false" ]]; then
                # Pure DHCP - IPAssignmentMode can be DHCP or StaticPool (user override)
                if [[ "$ip_mode" != "DHCP" ]] && [[ "$ip_mode" != "StaticPool" ]] && [[ -n "$ip_mode" ]]; then
                    echo "IPAssignmentMode is $ip_mode but expected DHCP or StaticPool for pure DHCP scenario"
                    return 1
                fi
            else
                # Mixed (static + DHCP) - IPAssignmentMode should be StaticPool
                if [[ "$ip_mode" != "StaticPool" ]] && [[ -n "$ip_mode" ]]; then
                    echo "IPAssignmentMode is $ip_mode but expected StaticPool for mixed static+DHCP scenario"
                    return 1
                fi
            fi
        fi

        # For pure DHCP scenarios, verify IPs are still assigned (by DHCP server)
        if [[ "$expect_static" == "false" ]] && [[ -z "$ip_addresses" ]]; then
            echo "DHCP enabled but no IP addresses assigned (DHCP may have failed)"
            return 1
        fi
    fi

    return 0
}

# Cleanup resources
cleanup_resources() {
    local namespace="$1"
    shift
    local vms=("$@")

    # Delete all VMs created during test run
    for vm_name in "${vms[@]}"; do
        if [[ -n "$vm_name" ]]; then
            kubectl delete vm "$vm_name" -n "$namespace" --ignore-not-found=true &>/dev/null || true
        fi
    done

    # Optionally delete secrets (commented out to preserve for future runs)
    # kubectl delete secret vm-cloud-init-bootstrap-data -n "$namespace" --ignore-not-found=true &>/dev/null || true
    # kubectl delete secret linuxprep-password -n "$namespace" --ignore-not-found=true &>/dev/null || true
}

# List all test cases
list_test_cases() {
    echo "Available Test Cases:"
    echo ""
    printf "%-10s %-50s %-15s %-15s %-15s\n" "Test ID" "Test Case Name" "Bootstrap" "IP Family" "Network"
    printf "%-10s %-50s %-15s %-15s %-15s\n" "--------" "$(printf '%.0s-' {1..50})" "----------" "----------" "----------"

    if [[ -z "${ALL_TEST_CASES:-}" ]]; then
        echo "Error: Test case registry not available"
        return 1
    fi

    for test_case in "${ALL_TEST_CASES[@]}"; do
        IFS='|' read -r tc_id tc_name bootstrap ip_family dhcp_config func network_category category_name interface_ip_families <<< "$test_case"
        printf "%-10s %-50s %-15s %-15s %-15s\n" "$tc_id" "$tc_name" "$bootstrap" "$ip_family" "$network_category"
    done
    echo ""
    echo "Total: ${#ALL_TEST_CASES[@]} test cases"
}

# Filter test cases by ID
filter_test_cases() {
    local requested_ids="$1"
    local filtered=()

    if [[ -z "$requested_ids" ]]; then
        # Return all test cases
        filtered=("${ALL_TEST_CASES[@]}")
    else
        # Parse comma-separated list
        IFS=',' read -ra REQUESTED_ARRAY <<< "$requested_ids"
        for requested_id in "${REQUESTED_ARRAY[@]}"; do
            requested_id=$(echo "$requested_id" | tr -d ' ' | tr '[:lower:]' '[:upper:]')  # Remove spaces and uppercase
            local found=false
            for test_case in "${ALL_TEST_CASES[@]}"; do
                IFS='|' read -r tc_id tc_name bootstrap ip_family dhcp_config func network_category category_name interface_ip_families <<< "$test_case"
                if [[ "$tc_id" == "$requested_id" ]]; then
                    filtered+=("$test_case")
                    found=true
                    break
                fi
            done
            if [[ "$found" == "false" ]]; then
                echo "Error: Test case '$requested_id' not found" >&2
                echo "Available test cases:" >&2
                for test_case in "${ALL_TEST_CASES[@]}"; do
                    IFS='|' read -r tc_id <<< "$test_case"
                    echo "  - $tc_id" >&2
                done
                return 1
            fi
        done
    fi

    # Output filtered test cases (one per line, will be read into array)
    printf '%s\n' "${filtered[@]}"
}

# Get network name for category
get_network_for_category() {
    local category="$1"
    case "$category" in
        "StaticPool") echo "${STATICPOOL_NETWORK:-${PRIMARY_NETWORK}}" ;;
        "DHCP") echo "${DHCP_NETWORK:-${PRIMARY_NETWORK}}" ;;
        "NoIPAM") echo "${NOIPAM_NETWORK:-${PRIMARY_NETWORK}}" ;;
        *) echo "${PRIMARY_NETWORK}" ;;
    esac
}

# Print results table
print_results_table() {
    printf "%-10s %-50s %-15s %-15s %-8s %s\n" "Test ID" "Test Case Name" "Bootstrap" "IP Family" "Status" "Reason"
    printf "%-10s %-50s %-15s %-15s %-8s %s\n" "--------" "$(printf '%.0s-' {1..50})" "----------" "----------" "------" "------"

    for result in "${RESULTS[@]}"; do
        IFS='|' read -r test_id test_name bootstrap ip_family status reason diagnostics <<< "$result"

        # Truncate long names
        test_name=$(echo "$test_name" | cut -c1-50)
        reason=$(echo "$reason" | cut -c1-50)

        # Color status - use %b format specifier to interpret escape sequences
        local status_display
        if [[ "$status" == "PASS" ]]; then
            status_display="${GREEN}PASS${NC}"
        else
            status_display="${RED}FAIL${NC}"
        fi

        # Use %b to interpret escape sequences in the status_display variable
        printf "%-10s %-50s %-15s %-15s %-8b %s\n" "$test_id" "$test_name" "$bootstrap" "$ip_family" "$status_display" "$reason"
    done

    # Print detailed diagnostics for failures
    local has_failures=false
    for result in "${RESULTS[@]}"; do
        IFS='|' read -r test_id test_name bootstrap ip_family status reason diagnostics <<< "$result"
        if [[ "$status" == "FAIL" ]] && [[ -n "$diagnostics" ]]; then
            if [[ "$has_failures" == "false" ]]; then
                echo ""
                echo "=========================================="
                echo "Detailed Failure Diagnostics"
                echo "=========================================="
                has_failures=true
            fi
            echo ""
            echo "--- $test_id: $test_name ---"
            echo -e "$diagnostics"
        fi
    done
}

