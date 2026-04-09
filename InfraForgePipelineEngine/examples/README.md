# Pipeline Engine — Examples

These JSON files demonstrate how client applications submit pipeline execution requests to the engine.

## Files

### `simple_deploy.json`
Minimal pipeline using only the built-in `noop` step type. Use this to verify the engine is running:

```bash
curl -X POST http://localhost:8100/api/pipelines/run \
  -H "Content-Type: application/json" \
  -d @examples/simple_deploy.json
```

### `infraforge_onboarding.json`
Replicates InfraForge's 12-step service onboarding pipeline. Requires the `infraforge-steps` plugin package installed, which would provide step handlers for:

| Module | Functions | Maps to InfraForge |
|--------|-----------|-------------------|
| `infraforge_steps.init` | `initialize_onboarding` | Step 1: Initialize |
| `infraforge_steps.deps` | `check_dependency_gates` | Step 2: Dependency check |
| `infraforge_steps.standards` | `analyze_standards` | Step 3: Standards analysis |
| `infraforge_steps.generators` | `generate_arm_template`, `generate_policy` | Steps 5-6: Template & policy generation |
| `infraforge_steps.governance` | `run_governance_review` | Step 7: Governance review |
| `infraforge_steps.deploy` | `validate_and_deploy`, `deploy_policy`, `cleanup_test_resources` | Steps 8, 10, 11 |
| `infraforge_steps.testing` | `run_smoke_tests` | Step 9: Infra testing |
| `infraforge_steps.healing` | `heal_arm_template`, `heal_policy`, `heal_governance_rejection` | Healing handlers |
| `infraforge_steps.catalog` | `promote_service` | Step 12: Promote to v1.0.0 |

### `autoiac_generate.json`
Replicates AutoIaC's validate → generate → validate-template flow. Requires the `autoiac-steps` plugin package:

| Module | Functions |
|--------|-----------|
| `autoiac_steps.validation` | `validate_services`, `validate_template_syntax` |
| `autoiac_steps.generate` | `generate_template` |

## Plugin Development

See [docs/PLUGIN_GUIDE.md](../docs/PLUGIN_GUIDE.md) for how to create step handler plugins.
