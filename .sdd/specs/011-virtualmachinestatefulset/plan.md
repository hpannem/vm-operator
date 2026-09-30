# Implementation Plan: VirtualMachineStatefulSet

- **Spec**: [`spec.md`](./spec.md)
- **Epic**: vmop-1837
- **Date**: 2026-09-30

## Summary

Add the `VirtualMachineStatefulSet` kind to `v1alpha6`, with a controller and webhooks that follow upstream `StatefulSet` behavior. The design reuses the `VirtualMachineReplicaSet` controller for VM ownership, label handling, and the Ready rule. Model details are in [`model.md`](./model.md).

## Technical context

- **Go version**: as the root `go.mod`.
- **API version touched**: `v1alpha6`. The `api/` module changes.
- **Modules touched**: root module and `api/`.
- **New dependencies**: none. `ControllerRevision` is in `k8s.io/api/apps/v1`. The revision hash and history helpers are copied in a small package, because `k8s.io/kubernetes` must not be a dependency.

## Constitution check

| Rule | Status | Notes |
|------|--------|-------|
| API compatibility | OK | New kind in the newest version. No existing field changes. |
| CRD markers, `doc.go`, deepcopy | OK | `make generate-go` and `make generate-manifests`. |
| Thin controllers | OK | The reconcile loop is in `controllers/virtualmachinestatefulset/`. The scale, update, revision, and PVC logic is in `pkg/`. |
| No direct vSphere calls | OK | The controller creates and updates `VirtualMachine` objects only. |
| `observedGeneration` and `Ready` condition | OK | See `model.md`. |
| Fan-out uses `CreateOrPatch` and controller reference | OK | VMs and PVCs have the set as controller owner. Exception for PVC owner references, see below. |
| Shared list fields | Needs care | The PVC retention policy edits `ownerReferences` on PVCs. Use `MergeFromWithOptimisticLock` and skip the write if nothing changed. |
| Webhooks | OK | Validation in `webhooks/virtualmachinestatefulset/`. CEL for simple rules. |
| Tests | OK | One test file and one suite file per package. Labels from `testlabels`. |
| E2E in same PR | OK | See test strategy. |
| Feature flag | OK | Reuses `Features.K8sWorkloadMgmtAPI` (capability `supports_k8s_workload_mgmt_api`). No new flag. |

## Project structure

```
api/v1alpha6/virtualmachinestatefulset_types.go
api/v1alpha6/virtualmachinestatefulset_conversion.go
controllers/virtualmachinestatefulset/
  virtualmachinestatefulset_controller.go
pkg/context/virtualmachinestatefulset_context.go
pkg/statefulset/
  identity.go       # names, labels, ordinals
  revision.go       # ControllerRevision create, hash, history, rollback
  scale.go          # OrderedReady and Parallel
  update.go         # RollingUpdate, partition, maxUnavailable, OnDelete
  storage.go        # PVC create, reattach, retention
webhooks/virtualmachinestatefulset/{mutation,validation}/
config/crd/, config/rbac/   # generated
docs/                       # user documentation
test/e2e/vmservice/virtualmachinestatefulset/
```

## API / CRD strategy

Additive: a new kind in `v1alpha6`. Follow the file layout of `virtualmachinereplicaset_types.go`. Register the kind in `pkg/crd`, as the ReplicaSet does. Add the `scale` subresource marker. CEL covers enums and range checks. The webhook covers the selector match, immutable fields, and volume name collisions.

## Controller / webhook impact

- **New controller.** It uses the canonical reconcile loop with a deferred patch. It watches the set, owned VMs, owned PVCs, and owned `ControllerRevision` objects. Mapper functions use field indexers.
- **Reconcile order.**
  1. Get or create the revisions. Compute `currentRevision`, `updateRevision`, and `collisionCount`.
  2. Adopt or release VMs by the selector.
  3. Create missing PVCs.
  4. Scale up or down by `podManagementPolicy`.
  5. Update VMs by `updateStrategy`.
  6. Apply the PVC retention policy.
  7. Trim revision history to `revisionHistoryLimit`.
  8. Write status.
- **Errors.** Use `RequeueError` while a VM is not yet Ready. Do not use conditions to control the flow.
- **RBAC.** Markers for `virtualmachinestatefulsets`, its `status` and `scale`, `controllerrevisions`, `persistentvolumeclaims`, and `virtualmachines`.
- **Webhooks.** A validator with an unexported type. A mutator for defaults if CRD defaults are not enough.
- **Metadata propagation.** The controller patches template labels and annotations to existing VMs. This does not change the revision hash. The hash must exclude template metadata. See the open point in `research.md`.
- **PVC owner references.** For `Delete` policies, the controller adds or removes the set or VM as owner of the PVC, as upstream does.

## Test strategy

- **Unit** (`testlabels.Controller`, `testlabels.API`): identity, scale up and down for both policies, `partition`, `maxUnavailable` rounding, `OnDelete`, revision create and trim, collision count, PVC create and retention, metadata propagation.
- **Integration** (`testlabels.EnvTest`): a full reconcile against envtest, and the webhook rules.
- **Webhook unit tests**: validator and mutator tables.
- **E2E** (`test/e2e/vmservice/virtualmachinestatefulset/`): one scenario for each acceptance criterion in `spec.md` that a cluster can show. Include `kubectl rollout history` and `undo`. Follow `e2e-sync-with-changes.md`.
- **Spec 009 as pattern.** Reuse its builder helpers and suite layout.

## Rollout / migration

- **Flag.** `Features.K8sWorkloadMgmtAPI`. The controller in `controllers/controllers.go` and the webhooks in `webhooks/webhooks.go` register under it, as the ReplicaSet does. The default does not change. E2E specs skip unless the FSS is on (`skipper.SkipUnlessK8sWorkloadMgmtAPIIsEnabled`).
- **Shared work with `VirtualMachineDeployment`.** Spec 010 (in another branch) adds availability computation from the `Ready` `lastTransitionTime`. Put the helper in a shared package so both controllers use it. Do not write it twice.
- **Migration.** None. The kind is new.
- **Docs and release note.** A user guide in `docs/` and a release note in the PR.
- **Follow-ups.** `VirtualMachineDeployment` (separate spec), start ordinals.

## Complexity tracking

| Violation | Why needed | Simpler alternative rejected because |
|-----------|------------|--------------------------------------|
| Copied revision helpers in `pkg/statefulset/revision.go` | Upstream logic lives in `k8s.io/kubernetes`. | Importing `k8s.io/kubernetes` adds a large dependency tree. |
