#!/usr/bin/env bash

# IPv6 and Dual-Stack Integration Test Automation Script
#
# This script runs all IPv6-only and dual-stack test cases from the integration test document.
# It creates VMs, verifies network configuration, and outputs results in a table format.

set -o errexit
set -o nounset
set -o pipefail

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source helper library
source "${SCRIPT_DIR}/lib.sh"

# Default values
CLEANUP=false
TIMEOUT="5m"
NAMESPACE=""
KUBECONFIG=""
PRIMARY_NETWORK=""
SECONDARY_NETWORK=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Results array: test_id|test_name|bootstrap|ip_family|status|reason
declare -a RESULTS

# VMs created during test run (for cleanup)
declare -a CREATED_VMS

# Parse command-line arguments
usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Required options:
  --namespace NAME              Namespace name
  --kubeconfig PATH             Path to kubeconfig file
  --primary-network NAME        Primary network name (vSphere Distributed Port Group)
  --secondary-network NAME      Secondary network name (vSphere Distributed Port Group)

Optional options:
  --cleanup                     Cleanup VMs after testing (default: false)
  --timeout DURATION            Timeout for VM Ready condition (default: 5m)

Examples:
  $0 --namespace telco-ns --kubeconfig ~/.kube/config \\
     --primary-network primary --secondary-network network-ipv6-test

  $0 --namespace telco-ns --kubeconfig ~/.kube/config \\
     --primary-network primary --secondary-network network-ipv6-test --cleanup
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --kubeconfig)
            KUBECONFIG="$2"
            shift 2
            ;;
        --primary-network)
            PRIMARY_NETWORK="$2"
            shift 2
            ;;
        --secondary-network)
            SECONDARY_NETWORK="$2"
            shift 2
            ;;
        --cleanup)
            CLEANUP=true
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate required arguments
if [[ -z "$NAMESPACE" ]] || [[ -z "$KUBECONFIG" ]] || [[ -z "$PRIMARY_NETWORK" ]] || [[ -z "$SECONDARY_NETWORK" ]]; then
    echo "Error: Missing required arguments"
    usage
fi

# Validate kubeconfig exists
if [[ ! -f "$KUBECONFIG" ]]; then
    echo "Error: Kubeconfig file not found: $KUBECONFIG"
    exit 1
fi

# Export kubectl context
export KUBECONFIG

# Derived values
VM_CLASS="best-effort-xsmall"
STORAGE_CLASS="wcpglobal-storage-profile"

# Initialize helper library
init_kubectl "$KUBECONFIG"

# Main execution
main() {
    echo "=========================================="
    echo "IPv6 and Dual-Stack Integration Tests"
    echo "=========================================="
    echo "Namespace: $NAMESPACE"
    echo "Primary Network: $PRIMARY_NETWORK"
    echo "Secondary Network: $SECONDARY_NETWORK"
    echo "Cleanup: $CLEANUP"
    echo "Timeout: $TIMEOUT"
    echo "=========================================="
    echo ""

    # Discover prerequisites
    echo "Discovering prerequisites..."
    VMI_ID=$(discover_vmi "$NAMESPACE")
    if [[ -z "$VMI_ID" ]]; then
        echo "Error: Could not find VMI in namespace $NAMESPACE or cluster-scoped"
        exit 1
    fi
    echo "Found VMI: $VMI_ID"
    echo ""

    # Verify VM Class exists
    if ! kubectl get vmclass "$VM_CLASS" -n "$NAMESPACE" &>/dev/null; then
        echo "Error: VM Class '$VM_CLASS' not found"
        exit 1
    fi

    # Verify StorageClass exists
    if ! kubectl get storageclass "$STORAGE_CLASS" -n "$NAMESPACE" &>/dev/null; then
        echo "Error: StorageClass '$STORAGE_CLASS' not found"
        exit 1
    fi

    # Create secrets
    echo "Creating secrets..."
    create_secrets "$NAMESPACE"
    echo ""

    # Source all test case files
    source "${SCRIPT_DIR}/cases-staticpool.sh"
    source "${SCRIPT_DIR}/cases-none.sh"
    source "${SCRIPT_DIR}/cases-user-overrides.sh"
    source "${SCRIPT_DIR}/cases-bootstrap-variations.sh"
    source "${SCRIPT_DIR}/cases-edge.sh"

    # Run test cases
    echo "Running test cases..."
    echo ""

    # Category 1: NetOP StaticPool Mode
    run_test_case "TC-001" "IPv6-Only StaticPool from NetOP" "Cloud-Init" "IPv6-only" \
        "tc001_ipv6_only" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-002" "Dual-Stack StaticPool from NetOP" "Cloud-Init" "Dual-stack" \
        "tc002_dual_stack" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-003" "Dual-Stack with WaitOnNetwork" "Cloud-Init" "Dual-stack" \
        "tc003_dual_stack_wait_network" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-004" "Multiple IPv6 Addresses StaticPool" "Cloud-Init" "IPv6-only" \
        "tc004_multiple_ipv6" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-005" "Dual-Stack with Multiple IPv6" "Cloud-Init" "Dual-stack" \
        "tc005_dual_stack_multi_ipv6" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    # Category 3: NetOP None Mode
    run_test_case "TC-006" "NoIPAM Mode from NetOP" "Cloud-Init" "N/A" \
        "tc006_noipam" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    # Category 4: User-Specified Overrides
    run_test_case "TC-007" "User Addresses Override NetOP" "Cloud-Init" "Dual-stack" \
        "tc007_user_addresses_override" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-008" "User Gateways Override NetOP" "Cloud-Init" "Dual-stack" \
        "tc008_user_gateways_override" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-009" "User Gateways None Clears NetOP" "Cloud-Init" "Dual-stack" \
        "tc009_gateway_none" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-010" "User DHCP6 Overrides NetOP" "Cloud-Init" "IPv6-only" \
        "tc010_dhcp6_override" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-011" "User DHCP4 + DHCP6 Override" "Cloud-Init" "Dual-stack" \
        "tc011_dhcp4_dhcp6_override" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-012" "User Addresses + NetOP Gateways" "Cloud-Init" "Dual-stack" \
        "tc012_gateway_backfill" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-013" "NetOP Addresses + User Gateways" "Cloud-Init" "Dual-stack" \
        "tc013_netop_addresses_user_gateways" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    # Category 5: Bootstrap Provider Variations
    run_test_case "TC-014" "GOSC IPv6-Only Static" "GOSC" "IPv6-only" \
        "tc014_gosc_ipv6_only" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-015" "GOSC Dual-Stack Static" "GOSC" "Dual-stack" \
        "tc015_gosc_dual_stack" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-016" "GOSC DHCP4 + DHCP6" "GOSC" "Dual-stack" \
        "tc016_gosc_dhcp4_dhcp6" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-017" "GOSC DHCP4 + Static IPv6" "GOSC" "Dual-stack" \
        "tc017_gosc_dhcp4_static_ipv6" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-018" "GOSC Static IPv4 + DHCP6" "GOSC" "Dual-stack" \
        "tc018_gosc_static_ipv4_dhcp6" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-019" "Cloud-Init Dual-Stack Multiple Addresses" "Cloud-Init" "Dual-stack" \
        "tc019_cloudinit_multiple_addresses" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    # Category 6: Edge Cases
    run_test_case "TC-020" "No Gateways Specified" "Cloud-Init" "Dual-stack" \
        "tc020_no_gateways" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-021" "IPv6-Only with Multiple Addresses" "Cloud-Init" "IPv6-only" \
        "tc021_ipv6_only_multiple" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-022" "User Override Partial Gateway Backfill" "Cloud-Init" "Dual-stack" \
        "tc022_partial_gateway_backfill" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    run_test_case "TC-023" "Dual-Stack Different Subnets" "Cloud-Init" "Dual-stack" \
        "tc023_different_subnets" "$NAMESPACE" "$PRIMARY_NETWORK" "$SECONDARY_NETWORK" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS"

    # Print results table
    echo ""
    echo "=========================================="
    echo "Test Results Summary"
    echo "=========================================="
    print_results_table

    # Cleanup if requested
    if [[ "$CLEANUP" == "true" ]]; then
        echo ""
        echo "Cleaning up resources..."
        cleanup_resources "$NAMESPACE" "${CREATED_VMS[@]}"
    fi

    # Exit with error if any test failed
    local failed_count=0
    for result in "${RESULTS[@]}"; do
        local status=$(echo "$result" | cut -d'|' -f5)
        if [[ "$status" == "FAIL" ]]; then
            ((failed_count++))
        fi
    done

    if [[ $failed_count -gt 0 ]]; then
        echo ""
        echo "Total failed tests: $failed_count"
        exit 1
    fi

    exit 0
}

# Run a single test case
run_test_case() {
    local test_id="$1"
    local test_name="$2"
    local bootstrap="$3"
    local ip_family="$4"
    local test_func="$5"
    shift 5
    local args=("$@")

    # Extract VM name from the generated spec (test functions use consistent naming)
    local vm_name_base=$(echo "$test_func" | sed 's/tc/tc/g')
    local vm_name=$(echo "$test_func" | sed 's/_/-/g' | sed 's/tc/tc/g')

    echo -n "Running $test_id: $test_name... "

    # Generate VM spec
    local vm_spec
    if ! vm_spec=$("$test_func" "${args[@]}"); then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Failed to generate VM spec")
        echo -e "${RED}FAIL${NC}"
        return 1
    fi

    # Extract VM name from spec (look for "name:" in metadata section)
    local vm_name=$(echo "$vm_spec" | grep -A 2 "metadata:" | grep "name:" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'" || echo "")

    # Apply VM spec
    if ! echo "$vm_spec" | kubectl apply -f - &>/dev/null; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Failed to apply VM spec")
        echo -e "${RED}FAIL${NC}"
        return 1
    fi

    if [[ -n "$vm_name" ]]; then
        CREATED_VMS+=("$vm_name")
    fi

    # Wait for VM Ready
    local vm_ready=false
    if kubectl wait --for=condition=Ready "vm/$vm_name" -n "$NAMESPACE" --timeout="$TIMEOUT" &>/dev/null; then
        vm_ready=true
    fi

    # Verify network configuration
    local verify_result
    if [[ "$vm_ready" == "true" ]]; then
        verify_result=$(verify_vm_network "$vm_name" "$NAMESPACE" "$ip_family")
    else
        verify_result="VM failed to become Ready"
    fi

    # Record result
    if [[ "$vm_ready" == "true" ]] && [[ -z "$verify_result" ]]; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|PASS|Network configuration verified")
        echo -e "${GREEN}PASS${NC}"
    else
        local reason="${verify_result:-VM failed to become Ready}"
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|$reason")
        echo -e "${RED}FAIL${NC}"
    fi

    # Cleanup VM if cleanup flag is set
    if [[ "$CLEANUP" == "true" ]]; then
        kubectl delete vm "$vm_name" -n "$NAMESPACE" --ignore-not-found=true &>/dev/null || true
    fi
}

# Run main function
main "$@"

