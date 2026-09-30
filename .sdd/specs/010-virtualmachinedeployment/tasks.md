# Tasks: VirtualMachineDeployment

- **Spec**: [`spec.md`](./spec.md)
- **Plan**: [`plan.md`](./plan.md)
- **Epic**: vmop-1700

The first release defines all API fields (T001). Until phases 6 to 8 ship, the webhook rejects a non-default value of `paused`, `progressDeadlineSeconds`, and `minReadySeconds`. Phases 6 to 9 are independent increments, in this order.

Every task has a `[vmop-NNN]` tag. The tickets are stories under the epic vmop-1700.

## Phase 1 — Setup

- [ ] T001 [vmop-1830] Define the `VirtualMachineDeployment` types, strategy, status (including `lastProgressTime`), subresource markers, condition and reason constants, and label and annotation constants in `api/v1alpha6/virtualmachinedeployment_types.go`. Add CEL rules: `self == oldSelf` on `spec.selector` (immutable), a non-empty selector (with `has()` guards on `matchLabels` and `matchExpressions`), and `rollingUpdate` only with type `RollingUpdate`
- [ ] T002 [vmop-1830] Run `make generate-go generate-manifests` to make the deepcopy, CRD, and RBAC files (`api/v1alpha6/zz_generated.deepcopy.go`, `config/crd/bases/vmoperator.vmware.com_virtualmachinedeployments.yaml`, `config/rbac/role.yaml`)
- [ ] T003 [P] [vmop-1830] Register the new type in the known types of the fake client. Add `DummyVirtualMachineDeployment` builders in `test/builder/`

## Phase 2 — Foundational

- [ ] T004 [vmop-4239] Add the typed context `VirtualMachineDeploymentContext` in `pkg/context/virtualmachinedeployment_context.go`
- [ ] T005 [P] [vmop-4239] Implement the template hash (with `collisionCount`), ReplicaSet matching (ignore the hash label), ReplicaSet naming with name cutting, and collision handling in `pkg/util/vmdeployment/hash.go`, with tests
- [ ] T006 [P] [vmop-4239] Implement revision numbers, `revision-history`, the annotation copy with its skip list, rollback matching, and history cleanup in `pkg/util/vmdeployment/revision.go`, with tests
- [ ] T007 [P] [vmop-1835] Implement the `RollingUpdate` math in `pkg/util/vmdeployment/rolling.go`, with tests. Include the fence-post rule, the starting size of a new ReplicaSet, the scale-up limit, `maxScaledDown`, unhealthy cleanup, and one step for each reconcile. The budgets use `availableReplicas`. Depends on T013
- [ ] T008 [P] [vmop-1836] Implement the `Recreate` logic in `pkg/util/vmdeployment/recreate.go`, with tests. The check for old VMs counts VMs that are being deleted. The controller creates no new ReplicaSet before that check passes
- [ ] T009 [P] [vmop-1834] Implement the scale-only path in `pkg/util/vmdeployment/proportional.go`, with tests. Include one active ReplicaSet, a saturated new ReplicaSet, proportional scaling, and scaling-event detection
- [ ] T010 [P] [vmop-4240] Rank VMs for removal like Kubernetes in `controllers/virtualmachinereplicaset/virtualmachinereplicaset_delete_policy.go`, with tests. The order is not-ready first, then recently-ready, then newer first
- [ ] T011 [P] [vmop-4240] Propagate the `deployment-name` label from the ReplicaSet to its VMs in `controllers/virtualmachinereplicaset/virtualmachinereplicaset_controller.go`, with tests. Resolve both `TODO`s

- [ ] T012 [vmop-4245] Add `spec.minReadySeconds` and `status.availableReplicas` to `VirtualMachineReplicaSet` in `api/v1alpha6/virtualmachinereplicaset_types.go`, and run `make generate-go generate-manifests`. Validate `minReadySeconds >= 0` in `webhooks/virtualmachinereplicaset/validation/virtualmachinereplicaset_validator.go`. This is in the core release, because the rolling update budgets use available VMs
- [ ] T013 [vmop-4245] Compute availability from the `lastTransitionTime` of the VM `Ready` condition in `controllers/virtualmachinereplicaset/virtualmachinereplicaset_controller.go`, with unit and integration tests. Set `availableReplicas`, and requeue for the time that remains. Depends on T012
- [ ] T014 [vmop-4245] Record the new fields in spec 009 in `.sdd/specs/009-virtualmachinereplicaset/tds.md`

## Phase 3 — Deployment lifecycle (DevOps user)

- [ ] T015 [US1] [vmop-1831] Add the controller scaffold in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go`. Include `AddToManager`, watches, the field indexer, mappers, the deferred patch, and the branch order (deleting, scaling event, strategy). Add no finalizer. Add only a placeholder for the paused branch. T027 implements it
- [ ] T016 [US1] [vmop-1833] Adopt and release ReplicaSets (patch `ownerReferences` with `MergeFromWithOptimisticLock`, and skip unchanged writes), find or create the new ReplicaSet, and set the revision and change-cause annotations in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go`. Depends on T005, T006
- [ ] T017 [US1] [vmop-1835] Run the strategy (`RollingUpdate`, `Recreate`, proportional scaling) and delete history beyond the limit in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go`. Depends on T007, T008, T009
- [ ] T018 [US1] [vmop-1833] Add the status, `Available`, `ReplicaFailure`, `Ready`, and events in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go`
- [ ] T019 [US1] [vmop-1831] Register the controller under `Features.K8sWorkloadMgmtAPI` in `controllers/controllers.go`
- [ ] T020 [P] [US1] [vmop-4241] Add unit tests in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller_test.go`, and the suite bootstrap in `virtualmachinedeployment_controller_suite_test.go`
- [ ] T021 [US1] [vmop-4241] Add integration tests (envtest) in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller_test.go`

## Phase 4 — Admission (Tenant admin)

- [ ] T022 [P] [vmop-1832] Add the mutation webhook (defaults) in `webhooks/virtualmachinedeployment/mutation/virtualmachinedeployment_mutator.go`, with tests
- [ ] T023 [P] [vmop-1832] Add the validation webhook in `webhooks/virtualmachinedeployment/validation/virtualmachinedeployment_validator.go`, with tests. It checks the selector-template match and that `maxSurge` and `maxUnavailable` are not both 0. It also rejects a non-default value of `paused`, `progressDeadlineSeconds`, and `minReadySeconds` for now. The other structural rules are in CEL (T001), not in this webhook
- [ ] T024 [vmop-1832] Register the webhooks in `webhooks/virtualmachinedeployment/webhooks.go` and `webhooks/webhooks.go`

## Phase 5 — E2E (Partner engineer, DevOps user)

- [ ] T025 [vmop-4242] Add E2E tests in `test/e2e/vmservice/virtualmachinedeployment/virtualmachinedeployment_test.go`. Cover create, `RollingUpdate`, `Recreate`, history, rollback, scale, cascade delete, and the label query
- [ ] T026 [P] [vmop-4242] Add Deployment manifest builders and helpers in `test/e2e/lib/vmoperator/` and `test/e2e/manifestbuilders/`

## Phase 6 — Increment: `paused`

- [ ] T027 [US1] [vmop-4243] Implement `spec.paused` in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go` and `pkg/util/vmdeployment/progress.go`, with unit and integration tests. It skips the rollout and keeps scaling. T029 sets the `DeploymentPaused` and `DeploymentResumed` conditions. Replace the placeholder from T015. Remove the webhook check for `paused`
- [ ] T028 [vmop-4243] Add the E2E test for pause and resume in `test/e2e/vmservice/virtualmachinedeployment/virtualmachinedeployment_test.go`

## Phase 7 — Increment: `progressDeadlineSeconds`

- [ ] T029 [US1] [vmop-4244] Implement the progress deadline in `pkg/util/vmdeployment/progress.go` and `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go`, with unit and integration tests. Include `lastProgressTime`, progress detection, `ProgressDeadlineExceeded`, the paused and resumed conditions, the timed requeue, and the deadline off at 2147483647. Remove the webhook check
- [ ] T030 [vmop-4244] Add the E2E test with a VM that never becomes ready in `test/e2e/vmservice/virtualmachinedeployment/virtualmachinedeployment_test.go`

## Phase 8 — Increment: `minReadySeconds`

- [ ] T031 [US1] [vmop-4245] Copy `minReadySeconds` from the Deployment to each ReplicaSet in `controllers/virtualmachinedeployment/virtualmachinedeployment_controller.go`, with unit and integration tests. Remove the webhook check. T012 and T013 (phase 2) already add the ReplicaSet fields and the availability logic, and the core release already uses `availableReplicas` for the rolling budgets, the status, and `Available`
- [ ] T032 [vmop-4245] Add the E2E test for `minReadySeconds` in `test/e2e/vmservice/virtualmachinedeployment/virtualmachinedeployment_test.go`

## Phase 9 — Increment: `kubectl vsphere vm rollout`

- [ ] T033 [vmop-4246] Agree on the repository, owner, and API dependency for the `kubectl vsphere` plugin work. Record them in `spec.md` and `plan.md`. File the plugin tickets under the epic
- [ ] T034 [US1] [vmop-4246] Implement `kubectl vsphere vm rollout status|history|undo|pause|resume` in the `kubectl vsphere` plugin repository (outside this repository), with unit tests
- [ ] T035 [vmop-4246] Add the E2E test for the `kubectl vsphere vm rollout` commands in `test/e2e/vmservice/virtualmachinedeployment/virtualmachinedeployment_test.go`

## Phase Final — Polish

- [ ] T036 [vmop-4247] Write the user docs in `docs/`. Cover rollout, history, rollback, pause, deadline, `minReadySeconds`, the stateless model, and shared data
- [ ] T037 [vmop-4247] Update `.sdd/INDEX.md`, set `spec.md` to `Implemented`, and write the release notes
