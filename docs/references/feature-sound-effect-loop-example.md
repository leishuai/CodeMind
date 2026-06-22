# Example: Add Sound Effect Selection to Playback

This example fixes the intended AutoMind loop semantics for a realistic feature request:

> Add a sound-effect selector to the playback screen. Users can open an effect panel, choose an effect such as Original/Reverb/Electronic/Airy, and playback should apply the selected effect.

## 1. TestCases

`TestCases.md` should split functional behavior from quality.

### Functional cases

| ID | Type | Intent |
|----|------|--------|
| TC-F01 | Key Path | Open playback screen. |
| TC-F02 | Key Path | Open the sound-effect panel. |
| TC-F03 | Key Path | Select an effect such as Reverb. |
| TC-F04 | Key Path | UI shows the selected effect. |
| TC-F05 | Key Path | Playback applies the selected effect. |
| TC-F06 | Smoke | Switch back to Original/no effect and playback still works. |
| TC-F07 | Smoke | Leave and return to playback screen; effect state is expected. |

### Quality cases

| ID | Category | Intent |
|----|----------|--------|
| TC-QP01 | performance | Opening the panel has timing evidence such as `duration_ms`. |
| TC-QP02 | performance | Switching effects does not cause an unacceptable playback gap. |
| TC-QU01 | ux | Current selected effect is visible and understandable. |
| TC-QS01 | stability | Rapid effect switching does not crash, timeout, or get stuck. |
| TC-QA01 | architecture | UI does not bypass playback/effect service to call raw audio engine details. |
| TC-QA02 | maintainability | Effect names/parameter mapping are not duplicated across UI files. |

## 2. Batches

A testcase is one declared check. A batch is the execution unit.

```text
Functional batch:
  TC-F01
  TC-F02
  TC-F03
  TC-F04
  TC-F05
  TC-F06
  TC-F07

Performance batch:
  TC-QP01
  TC-QP02

UX batch:
  TC-QU01

Stability batch:
  TC-QS01

Architecture/Maintainability batch:
  TC-QA01
  TC-QA02
```

## 3. Iteration

An iteration is one harness loop attempt. It is not one testcase.

Default V1 iteration order:

```text
Generator implementation/fix
  -> selected functional batch, completed first
  -> lightweight quality-check / relevant quality category batches
  -> optional semantic quality review
  -> one merged evaluation.json
  -> nextAction
```

Do not interleave quality after each functional testcase.

```text
Correct:
  TC-F01
  TC-F02
  TC-F03
  TC-F04
  -> then quality

Wrong:
  TC-F01 -> quality
  TC-F02 -> quality
  TC-F03 -> quality
```

## 4. Functional failure behavior

Functional batch runs before formal quality checks.

If a dependency testcase fails, fail fast:

```text
TC-F01 open playback screen: pass
TC-F02 open sound-effect panel: fail
TC-F03 select Reverb: not_run / skipped_dependency
TC-F04 selected effect visible: not_run / skipped_dependency
TC-F05 playback applies selected effect: not_run / skipped_dependency

nextAction = retry_generator
```

If a functional check is independent and non-blocking, continue-on-failure is allowed to collect more evidence before Generator retry.

Formal quality-check should not run until the selected functional batch is acceptable.

## 5. Quality failure behavior

If functional batch passes but quality fails:

```text
TC-QP01 pass
TC-QP02 fail: switching effects causes a 2s playback gap

nextAction = retry_generator
```

Generator may optimize effect switching, for example by changing from rebuilding the audio engine to dynamically switching effect nodes.

Because that changes runtime/product code, the next iteration starts with functional regression again. The target is the affected functional batch, not the entire product suite:

```text
next iteration:
  affected functional batch first
    - playback screen
    - effect panel
    - select effect
    - playback applies effect
    - switch back to Original
  -> quality-check / performance batch
  -> merged evaluation.json
```

Do not rerun unrelated product areas such as login/search/profile/payment unless the diff or requirement indicates they are affected.

Quality cases are grouped by category. If a category contains multiple cases, make the category acceptable as a batch; do not rerun functional regression after every single quality case.

## 6. Quality review

`quality_evaluator.py` is the default lightweight rule/script evaluator.

`templates/quality_review_prompt.md` is optional semantic model review. Use it only when:

- quality warnings are ambiguous;
- diff is large;
- architecture/performance risk is high;
- release-sensitive review is needed;
- the user explicitly asks for deeper review.

Quality review runs after functional evidence and lightweight quality-check exist, before final nextAction. Its output must merge into `qualityChecks[]`; it is not a second final verdict.

## 7. Finish condition

The task can finish only when the current acceptance point has:

```text
functional batch acceptable
AND
quality checks acceptable: pass or intentionally accepted warn
AND
no unresolved fail/block
```

A historical functional pass is not enough after runtime/product code changed.
