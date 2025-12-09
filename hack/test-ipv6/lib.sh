#!/usr/bin/env bash

# Helper function library for IPv6 integration tests

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

# Check VM Ready condition
verify_vm_ready() {
    local vm_name="$1"
    local namespace="$2"
    
    local ready_status=$(kubectl get vm "$vm_name" -n "$namespace" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    [[ "$ready_status" == "True" ]]
}

# Get VM IP addresses from status
get_vm_ip_addresses() {
    local vm_name="$1"
    local namespace="$2"
    
    kubectl get vm "$vm_name" -n "$namespace" -o jsonpath='{.status.network.interfaces[*].ipAddresses[*]}' 2>/dev/null | tr ' ' '\n' | grep -v '^$'
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

# Verify IP families (IPv6-only or dual-stack)
verify_ip_families() {
    local ip_addresses="$1"
    local expected_family="$2"
    
    local has_ipv4=false
    local has_ipv6=false
    
    while IFS= read -r ip; do
        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]]; then
            has_ipv4=true
        elif [[ "$ip" =~ : ]]; then
            has_ipv6=true
        fi
    done <<< "$ip_addresses"
    
    case "$expected_family" in
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

# Verify VM network configuration
verify_vm_network() {
    local vm_name="$1"
    local namespace="$2"
    local expected_ip_family="$3"
    
    # Check VM Ready
    if ! verify_vm_ready "$vm_name" "$namespace"; then
        echo "VM not Ready"
        return 1
    fi

    # Get IP addresses
    local ip_addresses
    ip_addresses=$(get_vm_ip_addresses "$vm_name" "$namespace")
    
    if [[ -z "$ip_addresses" ]] && [[ "$expected_ip_family" != "N/A" ]]; then
        echo "No IP addresses assigned"
        return 1
    fi

    # Verify IP families
    if ! verify_ip_families "$ip_addresses" "$expected_ip_family"; then
        echo "IP family mismatch (expected: $expected_ip_family)"
        return 1
    fi

    # Check NetworkInterface CR
    local netif_status
    if ! netif_status=$(get_networkinterface_status "$vm_name" "$namespace"); then
        echo "NetworkInterface CR not found"
        return 1
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

# Print results table
print_results_table() {
    printf "%-10s %-50s %-15s %-15s %-8s %s\n" "Test ID" "Test Case Name" "Bootstrap" "IP Family" "Status" "Reason"
    printf "%-10s %-50s %-15s %-15s %-8s %s\n" "--------" "$(printf '%.0s-' {1..50})" "----------" "----------" "------" "------"
    
    for result in "${RESULTS[@]}"; do
        local test_id=$(echo "$result" | cut -d'|' -f1)
        local test_name=$(echo "$result" | cut -d'|' -f2)
        local bootstrap=$(echo "$result" | cut -d'|' -f3)
        local ip_family=$(echo "$result" | cut -d'|' -f4)
        local status=$(echo "$result" | cut -d'|' -f5)
        local reason=$(echo "$result" | cut -d'|' -f6)
        
        # Truncate long names
        test_name=$(echo "$test_name" | cut -c1-50)
        
        # Color status
        if [[ "$status" == "PASS" ]]; then
            status="${GREEN}PASS${NC}"
        else
            status="${RED}FAIL${NC}"
        fi
        
        printf "%-10s %-50s %-15s %-15s %-8s %s\n" "$test_id" "$test_name" "$bootstrap" "$ip_family" "$status" "$reason"
    done
}

