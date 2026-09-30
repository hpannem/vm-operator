# Research: VirtualMachineStatefulSet

- **Spec**: [`spec.md`](./spec.md)

## Sources

- WIKI page 100764: the research page for the StatefulSet (version 56 at the time of writing).
- The upstream Kubernetes `StatefulSet` API and controller. This spec uses them as the reference.
- `api/v1alpha6/virtualmachinereplicaset_types.go` and `controllers/virtualmachinereplicaset/`: prior art in this repository.
- `.sdd/specs/009-virtualmachinereplicaset/`: the test plan for the ReplicaSet.

## Decisions taken from the research page

- Ordinals are `0` to `N-1`. Custom ordinals are not supported.
- The controller adds a label for the ordinal and a label for the name.
- `volumeClaimTemplates` create one PVC per replica.
- The user creates the headless `VirtualMachineService`. `serviceName` refers to it.
- `ControllerRevision` stores revisions. The status has `currentRevision`, `updateRevision`, `collisionCount`, and the upstream counts.
- A change to template metadata only does not cause a rollout.
- Non-goals: use of `spec.state`, autoscaling, and shared ReadWriteMany volumes.

## Decisions that follow upstream (override or extend the page)

The page and its source PDF conflict in places. The spec author decided to match upstream in each case.

| Topic | Page or PDF | Spec decision |
|-------|-------------|---------------|
| Revision history | The page says history is kept, and also says rollback works as upstream. | Keep `ControllerRevision`. Support `revisionHistoryLimit`, default `10`. Support `kubectl rollout history` and `undo`. |
| Update strategy | The PDF says `OnDelete` only for the first release of the Deployment. The page says both types. | Both types are in scope for the StatefulSet. The default is `RollingUpdate`. The Deployment stays out of this spec. |
| Pod management | The page says `OrderedReady` only, and `Parallel` is a follow-up. | Support both. The default is `OrderedReady`. |
| PVC retention | The page says the controller always keeps PVCs. | Support `persistentVolumeClaimRetentionPolicy`. The default is `Retain` for both values, which is the same result as the page. |

## Points to investigate during implementation

- **Ready rule (decided).** VMs have a readiness probe, so the `Ready` condition is the signal. The ReplicaSet `isVMReady` also counts a VM with no probe as Ready. Reuse it. If a user omits the probe, `OrderedReady` does not wait. This matches the ReplicaSet and is documented, not blocked.
- **`minReadySeconds` (decided).** Compute availability from the `lastTransitionTime` of the VM `Ready` condition. Spec 010 (`VirtualMachineDeployment`, other branch) uses the same rule and adds the fields to `VirtualMachineReplicaSet`. Share one helper.
- **Feature flag (decided).** `supports_k8s_workload_mgmt_api`, that is `Features.K8sWorkloadMgmtAPI`. The FSS name on VC is `WCP_VMService_K8s_Workload_Mgmt_API`.
- **Revision hash and metadata.** Upstream includes the whole Pod template in the hash. The research page says a metadata-only change must not roll out. The hash must therefore exclude template metadata, or the controller must patch metadata and reuse the current revision. Choose one and test it.
- **Rollback with metadata.** After `kubectl rollout undo`, decide whether template metadata also returns to the old value.
- **Upstream helper code.** Check the license and size of the code to copy for revision hashing and history. Prefer a small rewrite.
- **VM names.** Confirm that `<name>-<ordinal>` stays within the VM name and DNS limits for the longest supported set name.
- **Guest hostname.** A stable VM name is only part of a stable identity. Check whether the guest hostname follows the VM name.
- **PVC attach.** Confirm that a PVC that a deleted VM used can attach to the new VM with the same name, for each supported storage class.
- **Parallel and updates.** Upstream `Parallel` does not change update order or `maxUnavailable`. Confirm this.
