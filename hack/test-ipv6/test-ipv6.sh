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
STATICPOOL_NETWORK=""
DHCP_NETWORK=""
NOIPAM_NETWORK=""
LIST_ONLY=false
TEST_CASES=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Results array: test_id|test_name|bootstrap|ip_family|status|reason|diagnostics
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

Optional options:
  --staticpool-network NAME     Network for StaticPool mode tests (default: primary-network)
  --dhcp-network NAME           Network for DHCP mode tests (default: primary-network)
  --noipam-network NAME         Network for NoIPAM mode tests (default: primary-network)
  --cleanup                     Cleanup VMs after testing (default: false)
  --timeout DURATION            Timeout for VM Network Config Synced condition (default: 5m)
  --list                        List all available test cases and exit
  --test-case ID[,ID...]        Run specific test case(s) by ID (e.g., TC-001 or TC-001,TC-002)

Examples:
  $0 --namespace telco-ns --kubeconfig ~/.kube/config \\
     --primary-network primary

  $0 --namespace telco-ns --kubeconfig ~/.kube/config \\
     --primary-network primary --staticpool-network staticpool-net \\
     --dhcp-network dhcp-net --noipam-network noipam-net --cleanup
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
        --staticpool-network)
            STATICPOOL_NETWORK="$2"
            shift 2
            ;;
        --dhcp-network)
            DHCP_NETWORK="$2"
            shift 2
            ;;
        --noipam-network)
            NOIPAM_NETWORK="$2"
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
        --list)
            LIST_ONLY=true
            shift
            ;;
        --test-case)
            TEST_CASES="$2"
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
# Test case registry: TC_ID|TC_NAME|BOOTSTRAP|IP_FAMILY|DHCP_CONFIG|FUNCTION|NETWORK_CATEGORY|CATEGORY_NAME|INTERFACE_IP_FAMILIES
# INTERFACE_IP_FAMILIES: Format "eth0:IP_FAMILY" for single interface or "eth0:IP_FAMILY eth1:IP_FAMILY" for multi-interface
# If empty, defaults to "eth0:IP_FAMILY" using the IP_FAMILY field
declare -a ALL_TEST_CASES=(
    "TC-001|IPv6-Only StaticPool from NetOP|Cloud-Init|IPv6-only|none|tc001_ipv6_only|StaticPool|Category 1: NetOP StaticPool Mode|"
    "TC-002|Dual-Stack StaticPool from NetOP|Cloud-Init|Dual-stack|none|tc002_dual_stack|StaticPool|Category 1: NetOP StaticPool Mode|"
    "TC-003|Dual-Stack with WaitOnNetwork|Cloud-Init|Dual-stack|none|tc003_dual_stack_wait_network|StaticPool|Category 1: NetOP StaticPool Mode|"
    "TC-004|Multiple IPv6 Addresses StaticPool|Cloud-Init|IPv6-only|none|tc004_multiple_ipv6|StaticPool|Category 1: NetOP StaticPool Mode|"
    "TC-005|Dual-Stack with Multiple IPv6|Cloud-Init|Dual-stack|none|tc005_dual_stack_multi_ipv6|StaticPool|Category 1: NetOP StaticPool Mode|"
    "TC-006|NoIPAM Mode from NetOP|Cloud-Init|N/A|none|tc006_noipam|NoIPAM|Category 3: NetOP None Mode|"
    "TC-007|User Addresses Override NetOP|Cloud-Init|Dual-stack|none|tc007_user_addresses_override|StaticPool|Category 4: User-Specified Overrides|"
    "TC-008|User Gateways Override NetOP|Cloud-Init|Dual-stack|none|tc008_user_gateways_override|StaticPool|Category 4: User-Specified Overrides|"
    "TC-009|User Gateways None Clears NetOP|Cloud-Init|Dual-stack|none|tc009_gateway_none|StaticPool|Category 4: User-Specified Overrides|"
    "TC-010|User DHCP6 Overrides NetOP|Cloud-Init|IPv6-only|static+dhcp6|tc010_dhcp6_override|DHCP|Category 4: User-Specified Overrides|"
    "TC-011|User DHCP4 + DHCP6 Override|Cloud-Init|Dual-stack|dhcp4+dhcp6|tc011_dhcp4_dhcp6_override|DHCP|Category 4: User-Specified Overrides|"
    "TC-012|User Addresses + NetOP Gateways|Cloud-Init|Dual-stack|none|tc012_gateway_backfill|StaticPool|Category 4: User-Specified Overrides|"
    "TC-013|NetOP Addresses + User Gateways|Cloud-Init|Dual-stack|none|tc013_netop_addresses_user_gateways|StaticPool|Category 4: User-Specified Overrides|"
    "TC-014|GOSC IPv6-Only Static|GOSC|IPv6-only|none|tc014_gosc_ipv6_only|StaticPool|Category 5: Bootstrap Provider Variations|"
    "TC-015|GOSC Dual-Stack Static|GOSC|Dual-stack|none|tc015_gosc_dual_stack|StaticPool|Category 5: Bootstrap Provider Variations|"
    "TC-016|GOSC DHCP4 + DHCP6|GOSC|Dual-stack|dhcp4+dhcp6|tc016_gosc_dhcp4_dhcp6|DHCP|Category 5: Bootstrap Provider Variations|"
    "TC-017|GOSC DHCP4 + Static IPv6|GOSC|Dual-stack|static+dhcp4|tc017_gosc_dhcp4_static_ipv6|DHCP|Category 5: Bootstrap Provider Variations|"
    "TC-018|GOSC Static IPv4 + DHCP6|GOSC|Dual-stack|static+dhcp6|tc018_gosc_static_ipv4_dhcp6|DHCP|Category 5: Bootstrap Provider Variations|"
    "TC-019|Cloud-Init Dual-Stack Multiple Addresses|Cloud-Init|Dual-stack|none|tc019_cloudinit_multiple_addresses|StaticPool|Category 5: Bootstrap Provider Variations|"
    "TC-020|No Gateways Specified|Cloud-Init|Dual-stack|none|tc020_no_gateways|StaticPool|Category 6: Edge Cases|"
    "TC-021|IPv6-Only with Multiple Addresses|Cloud-Init|IPv6-only|none|tc021_ipv6_only_multiple|StaticPool|Category 6: Edge Cases|"
    "TC-022|User Override Partial Gateway Backfill|Cloud-Init|Dual-stack|none|tc022_partial_gateway_backfill|StaticPool|Category 6: Edge Cases|"
    "TC-023|Dual-Stack Different Subnets|Cloud-Init|Dual-stack|none|tc023_different_subnets|StaticPool|Category 6: Edge Cases|"
    "TC-024|Primary IPv4 + Secondary IPv6|Cloud-Init|Multi-interface|none|tc024_primary_ipv4_secondary_ipv6|StaticPool|Category 7: Primary IPv4 + Secondary Network|eth0:IPv4-only eth1:IPv6-only"
    "TC-025|Primary IPv4 + Secondary Dual-Stack|Cloud-Init|Dual-stack|none|tc025_primary_ipv4_secondary_dual_stack|StaticPool|Category 7: Primary IPv4 + Secondary Network|eth0:IPv4-only eth1:Dual-stack"
    "TC-026|Primary IPv4 + Secondary NoIPAM|Cloud-Init|Dual-stack|none|tc026_primary_ipv4_secondary_noipam|NoIPAM|Category 7: Primary IPv4 + Secondary Network|eth0:IPv4-only eth1:N/A"
    "TC-027|Primary IPv4 + Secondary DHCP|Cloud-Init|Dual-stack|dhcp4+dhcp6|tc027_primary_ipv4_secondary_dhcp|DHCP|Category 7: Primary IPv4 + Secondary Network|eth0:IPv4-only eth1:Dual-stack"
)

# Handle --list early (doesn't need other args)
if [[ "$LIST_ONLY" == "true" ]]; then
    # Source helper library for list function
    source "${SCRIPT_DIR}/lib.sh"
    # Export test case registry to lib.sh
    export ALL_TEST_CASES
    list_test_cases
    exit 0
fi

if [[ -z "$NAMESPACE" ]] || [[ -z "$KUBECONFIG" ]] || [[ -z "$PRIMARY_NETWORK" ]]; then
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
    # Set defaults for category-specific networks
    STATICPOOL_NETWORK="${STATICPOOL_NETWORK:-$PRIMARY_NETWORK}"
    DHCP_NETWORK="${DHCP_NETWORK:-$PRIMARY_NETWORK}"
    NOIPAM_NETWORK="${NOIPAM_NETWORK:-$PRIMARY_NETWORK}"
    echo "Primary Network: $PRIMARY_NETWORK"
    echo "StaticPool Network: $STATICPOOL_NETWORK"
    echo "DHCP Network: $DHCP_NETWORK"
    echo "NoIPAM Network: $NOIPAM_NETWORK"
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

    # Cleanup existing VMs if cleanup flag is set
    if [[ "$CLEANUP" == "true" ]]; then
        echo "Cleaning up existing VMs in namespace $NAMESPACE..."
        local existing_vms
        existing_vms=$(kubectl get vm -n "$NAMESPACE" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")
        if [[ -n "$existing_vms" ]]; then
            for vm in $existing_vms; do
                if [[ "$vm" =~ ^tc[0-9]+- ]]; then
                    echo "  Deleting existing VM: $vm"
                    kubectl delete vm "$vm" -n "$NAMESPACE" --ignore-not-found=true &>/dev/null || true
                fi
            done
        fi
        echo ""
    fi

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

    # Source all test case files first (needed for test case functions)
    source "${SCRIPT_DIR}/cases-staticpool.sh"
    source "${SCRIPT_DIR}/cases-none.sh"
    source "${SCRIPT_DIR}/cases-user-overrides.sh"
    source "${SCRIPT_DIR}/cases-bootstrap-variations.sh"
    source "${SCRIPT_DIR}/cases-edge.sh"
    source "${SCRIPT_DIR}/cases-primary-ipv4-secondary.sh"

    # Export variables needed by lib.sh functions
    export STATICPOOL_NETWORK DHCP_NETWORK NOIPAM_NETWORK PRIMARY_NETWORK
    export ALL_TEST_CASES

    # Filter test cases if --test-case specified
    local filtered_test_cases
    local filter_output
    if ! filter_output=$(filter_test_cases "$TEST_CASES"); then
        exit 1
    fi

    # Read filtered test cases into array (handle newlines properly)
    # Use mapfile to properly handle newlines and create array
    mapfile -t filtered_test_cases <<< "$filter_output"

    # Filter out any empty elements
    local temp_array=()
    for item in "${filtered_test_cases[@]}"; do
        if [[ -n "$item" ]]; then
            temp_array+=("$item")
        fi
    done
    filtered_test_cases=("${temp_array[@]}")

    # Verify test case functions exist
    for test_case in "${filtered_test_cases[@]}"; do
        if [[ -z "$test_case" ]]; then
            continue  # Skip empty lines
        fi
        IFS='|' read -r tc_id tc_name bootstrap ip_family dhcp_config func network_category category_name interface_configs <<< "$test_case"
        # Debug: check if parsing worked
        if [[ -z "$func" ]]; then
            echo "Error: Failed to parse test case. Raw string: '$test_case'" >&2
            echo "Parsed values: tc_id='$tc_id', func='$func'" >&2
            exit 1
        fi
        if ! declare -f "$func" >/dev/null 2>&1; then
            echo "Error: Test case function '$func' not found for $tc_id"
            exit 1
        fi
    done

    # Run test cases
    echo "Running test cases..."
    if [[ -n "$TEST_CASES" ]]; then
        echo "Filtered to: $TEST_CASES"
    fi
    echo ""

    # Run filtered test cases
    for test_case in "${filtered_test_cases[@]}"; do
        if [[ -z "$test_case" ]]; then
            continue  # Skip empty lines
        fi
        IFS='|' read -r tc_id tc_name bootstrap ip_family dhcp_config func network_category category_name interface_configs <<< "$test_case"

        # Debug: verify parsing
        if [[ -z "$func" ]]; then
            echo "Error: Failed to parse function name from test case: '$test_case'" >&2
            RESULTS+=("$tc_id|$tc_name|$bootstrap|$ip_family|FAIL|Failed to parse function name")
            continue
        fi

        # Verify function exists
        if ! declare -f "$func" >/dev/null 2>&1; then
            echo "Error: Test case function '$func' not found for $tc_id"
            RESULTS+=("$tc_id|$tc_name|$bootstrap|$ip_family|FAIL|Test function '$func' not found")
            continue
        fi

        # Get appropriate network for this test case category
        local network_name
        if ! network_name=$(get_network_for_category "$network_category" 2>/dev/null); then
            echo "Error: Failed to get network for category '$network_category'"
            RESULTS+=("$tc_id|$tc_name|$bootstrap|$ip_family|FAIL|Failed to get network for category")
            continue
        fi

        if [[ -z "$network_name" ]]; then
            echo "Error: Network name is empty for category '$network_category'"
            RESULTS+=("$tc_id|$tc_name|$bootstrap|$ip_family|FAIL|Network name is empty")
            continue
        fi

        # Run the test case
        # Check if this test case needs secondary network (Category 7)
        # For Category 7: primary network is always PRIMARY_NETWORK (IPv4), secondary is category-specific network
        local needs_secondary=false
        local secondary_network=""
        if [[ "$category_name" == "Category 7: Primary IPv4 + Secondary Network" ]]; then
            needs_secondary=true
            # Use the category-specific network for secondary interface
            secondary_network="$network_name"
        fi

        # Args: test_id, test_name, bootstrap, ip_family, dhcp_config, func, namespace, primary_network, secondary_network, vmi_id, vm_class, storage_class, interface_configs
        if [[ "$needs_secondary" == "true" ]]; then
            run_test_case "$tc_id" "$tc_name" "$bootstrap" "$ip_family" "$dhcp_config" \
                "$func" "$NAMESPACE" "$PRIMARY_NETWORK" "$secondary_network" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS" "$interface_configs"
        else
            run_test_case "$tc_id" "$tc_name" "$bootstrap" "$ip_family" "$dhcp_config" \
                "$func" "$NAMESPACE" "$network_name" "" "$VMI_ID" "$VM_CLASS" "$STORAGE_CLASS" "$interface_configs"
        fi
    done

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
    local expected_dhcp_config="$5"
    local test_func="$6"
    shift 6
    # Args after shift: namespace, primary_network, secondary_network (or empty), vmi_id, vm_class, storage_class, interface_configs
    local namespace="$1"
    local primary_network="$2"
    local secondary_network="$3"
    local vmi_id="$4"
    local vm_class="$5"
    local storage_class="$6"
    local interface_configs="${7:-}"

    echo -n "Running $test_id: $test_name... "

    # Verify test function exists
    if ! declare -f "$test_func" >/dev/null 2>&1; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Test function '$test_func' not found")
        echo -e "${RED}FAIL${NC}"
        return 1
    fi

    # Prepare arguments for test function
    # For regular test cases: namespace, primary_network, vmi_id, vm_class, storage_class
    # For secondary network test cases: namespace, primary_network, secondary_network, vmi_id, vm_class, storage_class
    local modified_args
    if [[ -n "$secondary_network" ]]; then
        # Secondary network test case
        modified_args=("$namespace" "$primary_network" "$secondary_network" "$vmi_id" "$vm_class" "$storage_class")
    else
        # Regular test case - primary_network is already the category-specific network
        modified_args=("$namespace" "$primary_network" "$vmi_id" "$vm_class" "$storage_class")
    fi

    # Generate VM spec
    local vm_spec
    set +o errexit
    # Debug: verify function and args
    if [[ -z "$test_func" ]]; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Test function name is empty")
        echo -e "${RED}FAIL${NC}"
        set -o errexit
        return 1
    fi

    # Call the test function
    vm_spec=$("$test_func" "${modified_args[@]}" 2>&1)
    local func_exit_code=$?
    set -o errexit

    if [[ $func_exit_code -ne 0 ]]; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Failed to generate VM spec (exit code: $func_exit_code)")
        echo -e "${RED}FAIL${NC}"
        if [[ -n "$vm_spec" ]]; then
            echo "Function output: $vm_spec" >&2
        fi
        return 1
    fi

    if [[ -z "$vm_spec" ]]; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|VM spec is empty")
        echo -e "${RED}FAIL${NC}"
        return 1
    fi

    # Extract VM name from spec (look for "name:" in metadata section)
    local vm_name=$(echo "$vm_spec" | grep -A 2 "metadata:" | grep "name:" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'" || echo "")

    if [[ -z "$vm_name" ]]; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Failed to extract VM name from spec")
        echo -e "${RED}FAIL${NC}"
        return 1
    fi

    # Apply VM spec
    if ! echo "$vm_spec" | kubectl apply -f - &>/dev/null; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|Failed to apply VM spec")
        echo -e "${RED}FAIL${NC}"
        return 1
    fi

    CREATED_VMS+=("$vm_name")
    echo -n "[$vm_name] "

    # Wait for VM Network Config Synced
    local vm_ready=false
    echo -n "Waiting for Network Config Synced..."
    if kubectl wait --for=condition="${VM_NETWORK_CONDITION}" "vm/$vm_name" -n "$NAMESPACE" --timeout="$TIMEOUT" &>/dev/null; then
        vm_ready=true
        echo -n "Ready. "
    else
        echo -n "Timeout. "
    fi

    # Verify network configuration
    # Use interface_configs from test case registry, or default to eth0 with test case IP family
    local interface_expected_families="$interface_configs"
    if [[ -z "$interface_expected_families" ]]; then
        interface_expected_families="eth0:$ip_family"
    fi

    local verify_result=""
    if [[ "$vm_ready" == "true" ]]; then
        # Capture both stdout and exit code - don't let errexit stop the script
        set +o errexit
        verify_result=$(verify_vm_network "$vm_name" "$NAMESPACE" "$interface_expected_families" "$expected_dhcp_config" 2>&1)
        local verify_exit_code=$?
        set -o errexit
        if [[ $verify_exit_code -ne 0 ]]; then
            # Function returned error, verify_result contains the error message
            # (keep verify_result as is, it already contains the error)
            :
        fi
    else
        verify_result="VM Network Config not Synced"
    fi

    # Record result
    if [[ "$vm_ready" == "true" ]] && [[ -z "$verify_result" ]]; then
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|PASS|Network configuration verified|")
        echo -e "${GREEN}PASS${NC}"
    else
        local reason="${verify_result:-VM Network Config not Synced}"
        # Collect detailed diagnostics on failure
        local diagnostics=""
        set +o errexit
        diagnostics=$(collect_full_diagnostics "$vm_name" "$NAMESPACE" "$ip_family" "$expected_dhcp_config" 2>&1)
        set -o errexit
        RESULTS+=("$test_id|$test_name|$bootstrap|$ip_family|FAIL|$reason|$diagnostics")
        echo -e "${RED}FAIL${NC}"
    fi

    # Cleanup VM if cleanup flag is set
    if [[ "$CLEANUP" == "true" ]]; then
        kubectl delete vm "$vm_name" -n "$NAMESPACE" --ignore-not-found=true &>/dev/null || true
    fi
}

# Run main function
main "$@"

