# Implementation Plan: VirtualMachineDeployment

- **Spec**: [`spec.md`](./spec.md)
- **Epic**: vmop-1700
- **Date**: 2026-09-29

## Summary

Add a `VirtualMachineDeployment` CRD and controller. They follow the upstream Kubernetes Deployment rollout method over `VirtualMachineReplicaSet`s, as [`spec.md`](./spec.md) describes. The controller supports `RollingUpdate` and `Recreate`. It keeps revision history and supports rollback by template edit. It also supports `paused`, `progressDeadlineSeconds`, `minReadySeconds`, and the `kubectl vsphere vm rollout` commands. The team delivers these one by one (see "Delivery order" in the spec).

## Technical context

- **Go version**: per root `go.mod`
- **API version(s) touched**: `v1alpha6` (new type)
- **Modules touched**: root module, `api/` (new type, deepcopy)
- **New dependencies**: none

## Constitution check

| Rule | Status | Notes |
|------|--------|-------|
| API compatibility | OK | New CRD. `VirtualMachineReplicaSet` gets two optional fields (`spec.minReadySeconds`, `status.availableReplicas`). The `VirtualMachineReplicaSet` type is in the code, but the `K8sWorkloadMgmtAPI` feature flag has never been enabled for users. No user depends on the type, so the change is safe. The defaults also keep today's behavior. |
| CRD markers and generated files | OK | `+kubebuilder:object:root=true`. Deepcopy comes from `make generate-go`. Manifests come from `make generate-manifests`. Both are checked in. |
| Thin controllers | OK | The controller in `controllers/virtualmachinedeployment/` orchestrates. Hashing, ReplicaSet selection, rolling and recreate math, and history cleanup live in `pkg/util/vmdeployment/`. |
| No direct vSphere calls | OK | The controller uses Kubernetes objects only. |
| `observedGeneration` and `Ready` | OK | The standard patch helper sets them. |
| Fan-out uses `CreateOrPatch` and controller reference | OK | ReplicaSets use `CreateOrPatch` and `SetControllerReference`. The Deployment is the only writer of `spec.replicas` on its ReplicaSets. `spec.replicas` is not a list field, so an optimistic lock is not necessary. The Deployment also writes the ReplicaSet annotations map (revision, change cause). A map merges key by key. Adoption and release of ReplicaSets change the `ownerReferences` list. They follow the upstream `ControllerRefManager` flow (claim, adopt, release), but patch with `client.MergeFromWithOptimisticLock` and skip the write when nothing changed, as the fan-in rule requires. This is a deliberate difference from upstream, which uses a UID precondition and no lock. A conflict returns an error, and the next reconcile retries. |
| Webhooks in `webhooks/` | OK | CEL covers the strategy enum, the minimums, selector immutability (`self == oldSelf`), the non-empty selector, and the `rollingUpdate` and `type` pairing. Go covers the selector-template match and the `maxSurge`/`maxUnavailable` check. |
| Testing layout | OK | One `_test.go` and one `_suite_test.go` for each package. `Label()` decorators come from `pkg/constants/testlabels`. |
| E2E in the same PR | OK | See the test strategy. |
| Feature flag | OK | The plan reuses `Features.K8sWorkloadMgmtAPI`. |
| Tickets and wiki masking | OK | Only `vmop-NNN` and `WIKI page <ID>` appear. |

## Project structure

```
api/v1alpha6/virtualmachinedeployment_types.go      (new)
api/v1alpha6/zz_generated.deepcopy.go               (regenerated)
config/crd/bases/vmoperator.vmware.com_virtualmachinedeployments.yaml (generated)
config/rbac/role.yaml                               (generated, new rules)
controllers/virtualmachinedeployment/               (new: controller, suite test, test)
controllers/controllers.go                          (register under K8sWorkloadMgmtAPI)
controllers/virtualmachinereplicaset/               (label propagation, removal order)
pkg/context/virtualmachinedeployment_context.go     (new typed context)
api/v1alpha6/virtualmachinereplicaset_types.go      (two new optional fields)
pkg/util/vmdeployment/                              (new: hash, revision, rolling, recreate, history, proportional, progress)
(outside this repo)                                 (`kubectl vsphere vm rollout` commands in the kubectl vsphere plugin)
webhooks/virtualmachinedeployment/{mutation,validation}/ (new)
webhooks/webhooks.go                                (register)
test/builder/                                       (dummy builders, fake known types)
test/e2e/vmservice/virtualmachinedeployment/        (new)
docs/                                               (user docs)
```

## API / CRD strategy

This is an additive new type in `v1alpha6` only. See [`model.md`](./model.md). No conversion webhook is necessary. CEL or kubebuilder validation covers the simple structural rules: the strategy enum, `replicas >= 0`, `revisionHistoryLimit >= 0`, selector immutability (`self == oldSelf`), the non-empty selector, and the `rollingUpdate` and `type` pairing. Go validation covers the selector-template match and the `maxSurge`/`maxUnavailable` check.

## Controller / webhook impact

### Reconcile design

The controller follows the canonical reconcile loop in [`operator-best-practices.md`](../../memory/operator-best-practices.md). The algorithm follows upstream `pkg/controller/deployment` (see `research.md`). `ReconcileNormal` is level-triggered. It branches in this order:

1. If the Deployment is deleting, update the status only and return. The controller adds no finalizer. Owner references make Kubernetes garbage-collect the ReplicaSets and VMs, as upstream does.
2. List the ReplicaSets that match the selector. Adopt orphans. Release owned ReplicaSets whose labels no longer match.
3. If a deadline is set, set the paused or resumed condition (`Progressing` Unknown, reason `DeploymentPaused` or `DeploymentResumed`). Do not overwrite `ProgressDeadlineExceeded`. T029 implements this.
4. If `spec.paused` is true, run the **scale-only path**, then clean up history, then update the status. Do not create a ReplicaSet. The controller scaffold (T015) has only a placeholder for this branch. T027 implements it.
5. If a **scaling event** is found, run the scale-only path, then update the status. A scaling event is an active ReplicaSet with a `desired-replicas` annotation that is not `spec.replicas`.
6. Otherwise, run the **strategy path**.

**Find or create the new ReplicaSet** (the paths use this step):
- The new ReplicaSet is the one whose template equals `spec.template` when the hash label is ignored. If more than one matches, pick the oldest by creation time. Every other owned ReplicaSet is old.
- If the new ReplicaSet exists, sync its annotations (copy the Deployment annotations with the skip list, revision, desired and max replicas) and its `minReadySeconds`. The revision is the highest old revision plus 1, when that is more than the current one. Copy the revision to the Deployment. Limit the `revision-history` annotation to 2000 characters. Drop the oldest entries first, as upstream does.
- If an old ReplicaSet matches, this is a rollback. Reuse that ReplicaSet, and add its old revision to `revision-history`.
- If no ReplicaSet matches and creation is allowed, create `<deployment>-<hash>`. Put the hash label on its metadata, selector, and template, and add the `deployment-name` label. The hash includes `collisionCount`. On a name clash with a different template, increase `status.collisionCount` and retry. The starting size is `min(replicas + maxSurge - total VMs, replicas)`, and not less than 0. On a create error, set `Progressing` to False with `ReplicaSetCreateError`.

**Scale-only path:**
- With one active ReplicaSet, or none, set the active (or newest, by creation time) ReplicaSet to `spec.replicas`. Do nothing if its size already matches.
- If the new ReplicaSet is saturated, scale the old ReplicaSets to 0. Saturated means its size and its desired-replicas annotation equal `spec.replicas`, and its `status.availableReplicas` equals `spec.replicas`.
- Otherwise, with `RollingUpdate`, split the change in proportion. The allowed total is `replicas + maxSurge` (0 when `replicas` is 0). Sort the active ReplicaSets by size. Put the newer first when adding and the older first when removing. Give each its share, and give the leftover to the largest. The share of a ReplicaSet is `round(size * (replicas + maxSurge) / max-replicas annotation) - size`, limited to the number of VMs still to add or remove. A ReplicaSet of size 0 gets no share. When `spec.replicas` is 0, the share is the whole size. If the `max-replicas` annotation is missing, use `status.replicas` of the Deployment in its place. A result below 0 becomes 0. Update the desired and max annotations.

**Strategy path, `RollingUpdate`** (one step for each reconcile):
1. Find or create the new ReplicaSet.
2. If the new ReplicaSet is larger than `spec.replicas`, scale it down to `spec.replicas`. Otherwise scale it up within `replicas + maxSurge`. If anything changed, update the status and stop.
3. Scale the old ReplicaSets down. `maxScaledDown = all VMs - minAvailable - unavailable VMs of the new ReplicaSet`. Remove unhealthy VMs from old ReplicaSets first, oldest ReplicaSet first (by creation time). Then remove available VMs while `available VMs > minAvailable`, where `minAvailable = replicas - maxUnavailable`.
4. If the rollout is complete, clean up history.
5. Update the status.

**Strategy path, `Recreate`:**
1. Find the new ReplicaSet, but do not create it.
2. Scale the old ReplicaSets to 0. If anything changed, update the status and stop.
3. If any old VM exists, update the status and stop. List the VMs by the `deployment-name` label that do not belong to the new ReplicaSet. A VM that is being deleted counts.
4. Create the new ReplicaSet if needed. Scale it to `spec.replicas`.
5. If the rollout is complete, clean up history. Update the status.

**History cleanup:** Do not count an old ReplicaSet that is being deleted toward the limit. Sort the other old ReplicaSets by revision. Delete the oldest ones while their number is more than `revisionHistoryLimit`. Skip a ReplicaSet in these cases, as upstream does (`cleanupDeployment` in `sync.go`):
- It is being deleted.
- Its `spec.replicas` is not 0.
- Its `status.replicas` is not 0.
- Its generation is newer than its observed generation.

**Status and progress:**
- Calculate the counts from the ReplicaSet statuses, set `Available`, and copy `ReplicaFailure`.
- If a deadline is set and the rollout is not complete, use the counts to find progress. Progress means more updated, fewer old, more ready, or more available VMs. When there is progress, set `lastProgressTime` and set `Progressing` to True with `ReplicaSetUpdated`.
- When the rollout is complete, set `NewReplicaSetAvailable`.
- When there is no progress and `lastProgressTime` is older than `progressDeadlineSeconds`, set `Progressing` to False with `ProgressDeadlineExceeded`.
- If there is no progress and no timeout yet, requeue with `RequeueError{After: lastProgressTime + deadline - now}`. Otherwise, rely on the watches.
- Emit events for scaling, for example `ScalingReplicaSet`.

There is no `ReconcileDelete` and no finalizer, as in upstream. A deleting Deployment gets a status update only. Owner references garbage-collect the ReplicaSets and VMs. This differs from the canonical reconcile template, which adds a finalizer. A finalizer would only add cleanup that Kubernetes already does.

The Deployment controls the size of every ReplicaSet. A deleted VM in an old ReplicaSet gets the same handling as upstream gives a deleted Pod.

### Watches

- `For(&VirtualMachineDeployment{})`
- `Owns(&VirtualMachineReplicaSet{})`
- `Watches(&VirtualMachineReplicaSet{})` with a mapper for orphan adoption. The mapper uses a field indexer on the owner name. It does not list the whole namespace.
- `Watches(&VirtualMachine{})` mapped through the `deployment-name` label. A change in VM readiness then starts the next rollout step.

### ReplicaSet controller change

- Propagate the label. When a ReplicaSet has the label `vmoperator.vmware.com/deployment-name`, copy it to each VM that the ReplicaSet creates or adopts. This resolves both `TODO`s in `virtualmachinereplicaset_controller.go`. When the label is absent, the behavior does not change.
- Change the removal order. Extend `randomDeletePolicy` in `virtualmachinereplicaset_delete_policy.go` with the Kubernetes ranking: not-ready before ready, recently-ready before older, newer before older, then name. This resolves the `TODO` about a Ready condition in that file. The API does not change.

### ReplicaSet controller: availability

Add `spec.minReadySeconds` and `status.availableReplicas` to `VirtualMachineReplicaSet`. In `updateStatus`, count a VM as available when its `Ready` condition is `True` and its `lastTransitionTime` is `minReadySeconds` or more in the past. When a ready VM is not yet available, requeue after the shortest time that remains for any such VM. If that time cannot be found, fall back to `minReadySeconds`, as upstream does. Also requeue after `minReadySeconds` when a VM changes from not ready to ready, as upstream does. The existing 15 second requeue while `readyReplicas` differs from `spec.replicas` stays. The shorter time wins. A VM with a `Ready` condition that is `True` is available when `minReadySeconds` is 0, or when the `lastTransitionTime` of the condition is set and is `minReadySeconds` or more in the past. A `Ready` condition with an unset `lastTransitionTime` means not available, as upstream does. A VM that the controller counts as implicitly ready (no readiness probe, so no `Ready` condition) is available at once. See the resolved decision in the spec. The Deployment copies its `minReadySeconds` to the new ReplicaSet, as upstream does. The ReplicaSet webhooks validate `minReadySeconds >= 0`. The controller keeps counting VMs that are being deleted in `status.replicas` and in the difference from `spec.replicas` (spec 009 SC22 and SC32). See the resolved decision in the spec. Spec 009 records the new fields. The ReplicaSet fields and this logic ship in the core release (delivery step 1), because the rolling update budgets use available VMs. Only the copy of `minReadySeconds` from the Deployment, and the removal of its webhook check, wait for delivery step 4.

### kubectl vsphere vm rollout

The `kubectl vsphere` plugin implements `kubectl vsphere vm rollout status|history|undo|pause|resume`. The plugin code is not in this repository. This repository provides what the commands need:

- `history` and `undo` use the revision annotations and the old ReplicaSets. `undo` copies the template of the chosen ReplicaSet back to the Deployment. It removes the hash label from the template and restores the annotations of that revision.
- `pause` and `resume` set `spec.paused`.
- `status` uses the conditions, the replica counts, and `observedGeneration`.

The controller does not depend on the plugin. The repository of the plugin and its API dependency are an open question in the spec.

### Webhooks

- Mutation: default `strategy.type=RollingUpdate`, `maxSurge=25%`, `maxUnavailable=25%`, `replicas=1`, `revisionHistoryLimit=10`, `progressDeadlineSeconds=600`, `minReadySeconds=0`, and `paused=false`.
- Until each increment ships, validation rejects a non-default value of `paused`, `progressDeadlineSeconds`, or `minReadySeconds`. Each increment removes its own check.
- Validation: `progressDeadlineSeconds` must be more than `minReadySeconds`.
- CEL: the selector is not empty (`(has(self.matchLabels) && size(self.matchLabels) > 0) || (has(self.matchExpressions) && size(self.matchExpressions) > 0)`). The `has()` guards are necessary, because both fields are optional and CEL errors on an absent field. `rollingUpdate` is allowed only with `RollingUpdate` (`self.type == 'RollingUpdate' || !has(self.rollingUpdate)`).
- Go validation: the selector must match `template.metadata.labels`. `maxSurge` and `maxUnavailable` are not both 0.
- CEL: the selector is immutable on update (`self == oldSelf`).

### RBAC

Kubebuilder markers for `virtualmachinedeployments` (all verbs), `virtualmachinedeployments/status`, `virtualmachinedeployments/finalizers`, `virtualmachinereplicasets` (all verbs), and `virtualmachines` (get, list, watch).

## Test strategy

- **Unit** (`testlabels.Controller`): Table tests for the rolling and recreate math, proportional scaling, hash stability, rollback matching, history cleanup, collision handling, adoption and release, and status and conditions. Use the fake client with `WithStatusSubresource`. Add unit tests for the webhook validator and mutator. Add unit tests for the new removal order in the ReplicaSet controller.
- **Integration** (`testlabels.Controller`, `testlabels.EnvTest`): An envtest suite with the ReplicaSet controller. Cover pause and resume, the progress deadline, and `minReadySeconds`. Cover create, `RollingUpdate` within the surge and unavailable limits, `Recreate`, rollback, history limit, scale during a rollout, adoption, and cascade delete.
- **E2E** (`test/e2e/vmservice/virtualmachinedeployment/`, mandatory):
  - Create with N replicas.
  - Roll out a new template with `RollingUpdate` and with `Recreate`. Make sure that old ReplicaSets stay at 0.
  - Roll back by template edit.
  - Pause and resume.
  - Test the progress deadline with a VM that never becomes ready.
  - Test `minReadySeconds`.
  - Use `kubectl scale`.
  - Delete the Deployment.

  Use the existing helpers in `lib/vmoperator` and `manifestbuilders`, per `test/e2e/README.md`.

## Rollout / migration

- Feature flag: the existing `Features.K8sWorkloadMgmtAPI`. The default does not change.
- No schema upgrade and no backfill.
- Partner communication: a release note for the new CRD. The note states that surge VMs use extra capacity during a rollout.
- Docs: a `docs/` page for rollout, history, and rollback. The page states the stateless model and how to share data (network file shares, or an existing `ReadWriteMany` MultiWriter volume). It also states that a template PVC with `ReadWriteOnce` fails to attach on the second replica.
- Each increment removes its webhook check and adds its tests in the same PR.

## Complexity tracking

None.
