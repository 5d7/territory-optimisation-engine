# Business use cases: step-by-step playbooks

Each playbook follows the same shape: the situation, the setup, the steps, what to read, and what to take to the decision-makers. Setting names refer to the Settings cell in the notebook.

General rule: **start in soft mode**, read the coverage card and the rule scorecard before the move list, and treat the move count as a deliberate decision.

---

## User-story example: redesigning a district

> **As the EMEA RevOps lead, I want to redesign the EMEA Central district after adding a ninth AE, so that workload and growth opportunities are distributed fairly without disrupting strategic deals already in flight.**

### Starting situation

- 720 accounts are currently shared by eight AEs; a ninth AE starts next month.
- Two AEs are more than 20% above the district's average workload.
- Growth Engine and Burn Risk accounts are concentrated with a small number of AEs.
- 18 accounts have late-stage opportunities and must stay with their current owner.
- Leadership will accept at most 15% of currently owned accounts moving in this planning cycle.

### Prepare the input

1. Set `Region` to `EMEA Central` on the district's accounts and set the same value in `Region Alignment` for all nine AEs.
2. Populate `Spend Map` with values such as `Growth Engine`, `Burn Risk`, `Steady State` and `Long Shot`.
3. Set `Locked` to `Yes` for the 18 in-flight strategic deals.
4. Leave the new AE with no current accounts; the engine can build their starting book.
5. Run the baseline once and resolve all blocking data-quality errors before comparing alternatives.

The engine never assigns an account across `Region` values. If the redesign changes district boundaries—for example, splitting `EMEA Central` into `EMEA Central North` and `EMEA Central South`—prepare one input workbook per proposed boundary design and compare the exported summaries. Use the scenario cell below when the account-to-region boundary stays fixed and only planning rules change.

### Compare three planning scenarios

Replace the example dictionary in the scenario cell with:

```python
scenarios = {
    "Low-disruption rebalance": dict(
        allowed_workload_variance_pct=20,
        stability_bonus=2500,
        max_moves_pct=10,
        max_in_per_ae=20,
        max_out_per_ae=20,
    ),
    "Balanced redesign": dict(
        allowed_workload_variance_pct=15,
        stability_bonus=1500,
        max_moves_pct=15,
        max_in_per_ae=30,
        max_out_per_ae=30,
    ),
    "Workload-first redesign": dict(
        allowed_workload_variance_pct=10,
        stability_bonus=500,
        max_moves_pct=25,
    ),
}
```

Run these with a short solve time for comparison, then rerun the preferred scenario with the normal solve time before publishing it.

### Decision gates

Choose a plan only if:

1. Coverage is 100%, or every unassigned account has an explicitly accepted reason.
2. No locked account moves.
3. The move rate stays within leadership's agreed limit.
4. Remaining workload or Spend Map breaches are visible in the scorecard and have an owner who accepts them.
5. Regional leaders review the Moves sheet and record Approve, Reject or Lock decisions.

In this user story, the **Balanced redesign** is the first candidate because its 15% move cap matches leadership's limit. It is not automatically the winner: if it leaves material workload breaches, compare the specific extra moves required by the workload-first scenario before making the trade-off.

**Take to leadership.** A one-page comparison of coverage, move rate, remaining rule breaches and workload spread, followed by the proposed Moves sheet with a reason for every ownership change.

---

## 1. Annual territory and coverage planning

**Situation.** The new fiscal year needs every account assigned, workloads balanced and specialist coverage in place before quotas are released.

**Setup.** Full account export and the AE roster for all regions. Soft mode. Defaults for role limits, then adjust to the new year's policy.

**Steps**
1. Export accounts and AEs into the two-sheet workbook and run the data-quality checks.
2. Fix any blocking errors (duplicate IDs, missing columns, unknown AE names).
3. Set role limits, the workload band and portfolio caps to match the new plan year.
4. Run the review with a generous solve time per region.
5. Read coverage, then region status, then the rule scorecard.
6. Iterate on the one or two rules that remain breached. Raise their weight or relax their limit, and rerun.
7. Export the workbook and send the Moves sheet to regional sales leaders.
8. Collect Approve, Reject and Lock decisions, then rerun to produce the final plan.

**Take to leadership.** Coverage percentage, the rules still outside tolerance and by how much, and a reasoned move for every account that changes hands.

---

## 2. Mid-year rebalancing with minimal disruption

**Situation.** Pipeline has shifted and some reps are saturated, but leadership wants a fix, not a reorganisation.

**Setup.** Current ownership as the starting point. Mark in-flight negotiations as `Locked`.

**Steps**
1. Update the accounts file with current owners and the `Locked` flag for sensitive deals.
2. Raise `stability_bonus` so keeping an account with its owner is strongly preferred.
3. Optionally set `max_moves_pct`, `max_in_per_ae` and `max_out_per_ae` to cap the disruption.
4. Run the review and check coverage first, because tight caps can leave accounts unassigned.
5. Compare against a looser scenario using the scenario comparison cell.
6. Choose the setting that restores the band with the fewest moves.

**Take to leadership.** The smallest set of moves that brings reps back inside the band, with locked deals untouched.

---

## 3. Attrition and new-hire coverage

**Situation.** A rep leaves or a new cohort starts, and their accounts need a home, or a fair starting book.

**Steps**
1. Update the AE roster: remove departed reps, add new hires with their role and region.
2. Leave the departed rep's name as the Current AE on their accounts. The engine detects that the owner is no longer available and reassigns those accounts, labelling them "Reassigned (owner unavailable)" in the Moves sheet.
3. Run the review.
4. Open the AE Summary to see accounts and effort per AE after the plan.
5. Check the new hire's starting book against the Tier 3 minimum and the workload band.

Note that these reassignments cannot be rejected by managers, because every account must have an owner.

**Take to leadership.** A coverage plan that spreads orphaned accounts fairly instead of defaulting to the nearest manager.

---

## 4. Specialist alignment

**Situation.** A product line needs specialist coverage, and those accounts sit with generalists.

**Steps**
1. Flag specialists in `AE_Roles` with `Is_AI_Specialist` and mark focus accounts in `Product Focus`.
2. Keep `ai_specialists_only` on.
3. Run the review.
4. Read the matching scorecard line: how many AEs held non-aligned accounts before, and after.
5. Spot-check the move reasons for the specialist moves.

**Take to leadership.** A before and after count of accounts covered by specialists, with the reason on each move.

---

## 5. Portfolio risk and growth balancing

**Situation.** Some reps hold too many at-risk accounts while others carry heavy growth accounts.

**Steps**
1. Make sure each account has a Spend Map value (for example Burn Risk, Growth Engine, Steady State).
2. Set `max_burn_risk_per_ae`, `max_growth_engines` and `growth_engine_effort_cap`.
3. Choose `growth_cap_basis` to count total effort or growth-only effort against the cap.
4. Run the review and read the Burn Risk and Growth Engine columns in the AE Summary.

**Take to leadership.** Evidence that risk and growth workload is spread deliberately, not by accident of history.

---

## 6. Scenario analysis for leadership

**Situation.** Leadership asks "what if we cap accounts at 120?" or "what if we allow less churn?".

**Steps**
1. Run the baseline review.
2. In the scenario cell, edit the dictionary to define two or three alternative settings.
3. Run the scenarios with a short solve time per scenario for a fast comparison.
4. Compare coverage, moves and breaches side by side.
5. Re-run the chosen scenario with the full solve time before presenting.

**Take to leadership.** A quantified trade-off between disruption and rule compliance, instead of an argument.

---

## 7. M&A and segmentation changes

**Situation.** An acquisition or segmentation change adds accounts or changes which roles may hold them.

**Steps**
1. Append the new accounts to the Accounts sheet, or mark them with a blank owner.
2. Update `Role` values in `AE_Roles` for any new role definitions.
3. Run the review by region.
4. Check the Unassigned sheet for accounts that could not be placed, and why.

**Take to leadership.** A reasoned integration of the new book into existing territories.

---

## 8. Quarterly review cycle

**Situation.** Territory decisions are revisited every quarter, and nobody remembers why the last choices were made.

**Steps**
1. Run the review and send the workbook to managers.
2. Managers set Approve or Reject on each move in the Manager Decision column (blank counts as Approve) and put `Yes` in Lock next cycle for accounts to freeze.
3. Upload the reviewed workbook to the "next cycle" cell. It builds `Next_Cycle_Input.xlsx`, where approved moves become the new Current AE and rejected moves stay with the current AE and are locked.
4. Point the input file at that workbook and rerun with demo data switched off. Headcount changes, new accounts and revised ratings flow through.
5. Archive each cycle's workbook. The Settings and Run Summary sheets make every run reproducible.

**Take to leadership.** An audit trail of proposals, manager decisions and rule outcomes over time.

---

## Good operating practice

- Treat **coverage** as a gate. A tidy scorecard with unassigned accounts is not a finished plan.
- Fix the `random_seed` when comparing scenarios so differences come from settings, not from chance.
- Give the solver enough time before judging the plan. A `FEASIBLE` region may improve with more time.
- Keep manager decisions in the workbook so each cycle builds on the last.
- Never commit real input or output workbooks to a public repository.
