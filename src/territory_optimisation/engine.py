"""Territory optimisation engine, independent of notebooks and destinations."""

from __future__ import annotations
# =====================================================================
#  TERRITORY OPTIMISATION ENGINE v2
#  Sections: 1 Config | 2 Data | 3 Model | 4 Rule checks | 5 Orchestrator
#            6 Dashboard | 7 Excel export | 8 Review loop & scenarios
# =====================================================================
import os, time, uuid, datetime as dt
from dataclasses import dataclass, field, asdict, replace
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
from ortools.sat.python import cp_model

try:
    from google.colab import files as colab_files
    IN_COLAB = True
except Exception:
    colab_files, IN_COLAB = None, False

EFFORT, REV = 'Estimated Effort (Hours)', 'Annual Revenue'
ACC_REQUIRED = ['Region', 'Product Focus', 'Current AE', REV, EFFORT,
                'Status', 'Tier', 'Spend Map', 'Vertical']
AE_REQUIRED = ['AE Name', 'Role', 'Region Alignment', 'Is_AI_Specialist']
KNOWN_ROLES = ['Strategic', 'Account Manager', 'Enterprise Acquisition']
ACC_EXTRA_COLS = ['Proposed AE', 'Action', 'Move Reason', 'Solve Status']


class DataValidationError(ValueError):
    """Raised when input data contains blocking validation errors."""

    def __init__(self, message, data_quality):
        super().__init__(message)
        self.data_quality = data_quality.copy()

RULE = {  # internal key -> plain-English label
    'cap': 'Max accounts per AE',
    'tier3': 'Min Tier 3 accounts per AE',
    'burn': 'Max Burn Risk accounts per AE',
    'strat': 'Strategic: customers >= prospects',
    'amp': 'Account Manager: max prospects',
    'vert': 'Max accounts per vertical',
    'grow': 'Growth Engine effort cap',
    'band_lo': 'Workload floor (min effort hours)',
    'band_hi': 'Workload ceiling (max effort hours)',
    'moves': 'Max total moves (region)',
    'in': 'Max accounts moved IN per AE',
    'out': 'Max accounts moved OUT per AE',
    'coverage': 'Every account must be assigned',
    'acq': 'Acquisition AEs: no customers / Tier 1',
    'ai': 'Enterprise AI accounts need an AI specialist',
    'lock': 'Locked accounts must not move',
}


# ---------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------
@dataclass
class Config:
    # data
    use_demo_data: bool = True
    input_file: str = 'Enterprise_Scale_Accounts.xlsx'
    accounts_sheet: str = 'Accounts'
    aes_sheet: str = 'AE_Roles'
    demo_seed: int = 7
    # --- original business rules ---
    global_max_accounts: int = 150
    strategic_max_accounts: int = 80
    am_max_prospects: int = 40
    ai_specialists_only: bool = True
    min_tier_3_per_ae: int = 20
    max_accounts_per_vertical: int = 40
    max_burn_risk_per_ae: int = 25
    max_growth_engines: int = 25
    growth_engine_effort_cap: int = 3000
    growth_cap_basis: str = 'total'          # 'total' (original) or 'growth_only'
    allowed_workload_variance_pct: int = 20
    max_time_per_region_seconds: int = 120
    # --- new controls ---
    constraint_mode: str = 'soft'            # 'soft' = bend rules & report, 'hard' = original behaviour
    stability_bonus: int = 1000              # reward per account that keeps its current owner
    balance_weight: int = 5                  # pull towards equal workload (per hour of deviation)
    base_value_per_account: int = 3000       # value of assigning ANY account (keeps $0 prospects covered)
    revenue_unit: int = 1000                 # revenue is divided by this in the score
    max_moves_pct: int = 100                 # cap on % of owned accounts that may change owner (100 = off)
    max_in_per_ae: int = 0                   # 0 = off
    max_out_per_ae: int = 0                  # 0 = off
    require_full_coverage: bool = False      # True = every account must get an owner
    respect_locked: bool = True              # honour the optional 'Locked' column
    # --- solver ---
    random_seed: int = 42
    num_workers: int = 0                     # 0 = auto (at least 8)
    relative_gap_limit: float = 0.005        # stop when within 0.5% of the proven best
    parallel_regions: bool = False
    # --- penalty weights used in soft mode (higher = rule is protected more) ---
    w_cap: int = 20000
    w_tier3: int = 5000
    w_burn: int = 5000
    w_strategic_mix: int = 8000
    w_am_prospects: int = 8000
    w_vertical: int = 5000
    w_growth_cap: int = 50                   # per hour over cap
    w_effort_band: int = 50                  # per hour outside the allowed band
    w_moves: int = 8000
    w_inout: int = 5000
    output_prefix: str = 'Optimized_Enterprise_Territories'

    def validate(self):
        if self.constraint_mode not in ('soft', 'hard'):
            raise ValueError("constraint_mode must be 'soft' or 'hard'")
        if self.growth_cap_basis not in ('total', 'growth_only'):
            raise ValueError("growth_cap_basis must be 'total' or 'growth_only'")
        if not 0 <= self.max_moves_pct <= 100:
            raise ValueError('max_moves_pct must be 0-100')
        if not 0 <= self.allowed_workload_variance_pct <= 100:
            raise ValueError('allowed_workload_variance_pct must be 0-100')
        positive = {
            'global_max_accounts': self.global_max_accounts,
            'strategic_max_accounts': self.strategic_max_accounts,
            'growth_engine_effort_cap': self.growth_engine_effort_cap,
            'max_time_per_region_seconds': self.max_time_per_region_seconds,
            'revenue_unit': self.revenue_unit,
        }
        non_negative = {
            'am_max_prospects': self.am_max_prospects,
            'min_tier_3_per_ae': self.min_tier_3_per_ae,
            'max_accounts_per_vertical': self.max_accounts_per_vertical,
            'max_burn_risk_per_ae': self.max_burn_risk_per_ae,
            'max_growth_engines': self.max_growth_engines,
            'allowed_workload_variance_pct': self.allowed_workload_variance_pct,
            'stability_bonus': self.stability_bonus,
            'balance_weight': self.balance_weight,
            'base_value_per_account': self.base_value_per_account,
            'max_in_per_ae': self.max_in_per_ae,
            'max_out_per_ae': self.max_out_per_ae,
            'num_workers': self.num_workers,
            **{name: getattr(self, name) for name in (
                'w_cap', 'w_tier3', 'w_burn', 'w_strategic_mix', 'w_am_prospects',
                'w_vertical', 'w_growth_cap', 'w_effort_band', 'w_moves', 'w_inout')},
        }
        bad_positive = [name for name, value in positive.items() if value <= 0]
        bad_non_negative = [name for name, value in non_negative.items() if value < 0]
        if bad_positive:
            raise ValueError(f"Settings must be > 0: {', '.join(bad_positive)}")
        if bad_non_negative:
            raise ValueError(f"Settings must be >= 0: {', '.join(bad_non_negative)}")
        if not 0 <= self.relative_gap_limit <= 1:
            raise ValueError('relative_gap_limit must be between 0 and 1')
        return self


# ---------------------------------------------------------------------
# 2. DATA: demo generator, loader, validation
# ---------------------------------------------------------------------
def make_demo_data(seed=7, regions=('AMER', 'EMEA', 'APAC'), aes_per_region=10, accounts_per_ae=85):
    if aes_per_region < 1:
        raise ValueError('aes_per_region must be at least 1')
    if accounts_per_ae < 1:
        raise ValueError('accounts_per_ae must be at least 1')
    rng = np.random.default_rng(seed)
    verticals = ['Finance', 'Retail', 'Health', 'Telecom', 'Energy', 'Public', 'Media', 'Industrial']
    ae_rows = []
    for r in regions:
        n_strategic = max(1, round(aes_per_region * 0.3))
        n_am = max(1, round(aes_per_region * 0.4)) if aes_per_region > 1 else 0
        n_am = min(n_am, aes_per_region - n_strategic)
        roles = (['Strategic'] * n_strategic + ['Account Manager'] * n_am +
                 ['Enterprise Acquisition'] * (aes_per_region - n_strategic - n_am))
        for k, role in enumerate(roles):
            ae_rows.append({'AE Name': f'{r}-AE-{k + 1:02d}', 'Role': role, 'Region Alignment': r,
                            'Is_AI_Specialist': bool(rng.random() < 0.4)})
    ae = pd.DataFrame(ae_rows)
    base_eff = {'Tier 1': 55, 'Tier 2': 30, 'Tier 3': 12}
    base_rev = {'Tier 1': 900_000, 'Tier 2': 300_000, 'Tier 3': 60_000}
    rows, n_total = [], 0
    for r in regions:
        names = ae.loc[ae['Region Alignment'] == r, 'AE Name'].to_numpy()
        n = aes_per_region * accounts_per_ae
        w = rng.dirichlet(np.ones(len(names)) * 1.5)          # uneven current ownership
        cur = rng.choice(names, size=n, p=w).astype(object)
        tier = rng.choice(['Tier 1', 'Tier 2', 'Tier 3'], n, p=[.2, .35, .45])
        status = rng.choice(['Customer', 'Prospect'], n, p=[.55, .45])
        for k in range(n):
            n_total += 1
            t = tier[k]
            rows.append({
                'Account ID': f'ACC-{n_total:05d}', 'Account Name': f'Company {n_total}', 'Region': r,
                'Product Focus': rng.choice(['Enterprise AI', 'Core Platform', 'Data & Analytics'], p=[.2, .5, .3]),
                'Current AE': cur[k],
                REV: int(base_rev[t] * rng.lognormal(0, .5) * (1 if status[k] == 'Customer' else .4)),
                EFFORT: int(max(2, base_eff[t] * rng.lognormal(0, .35))),
                'Status': status[k], 'Tier': t,
                'Spend Map': rng.choice(['Growth Engine', 'Burn Risk', 'Steady State', 'Long Shot'], p=[.25, .15, .35, .25]),
                'Vertical': rng.choice(verticals), 'Locked': 'No'})
    acc = pd.DataFrame(rows)
    orphan = rng.random(len(acc)) < .03
    blank = (~orphan) & (rng.random(len(acc)) < .03)
    acc.loc[orphan, 'Current AE'] = 'Former Rep'      # owner who left
    acc.loc[blank, 'Current AE'] = ''                # brand-new account
    lock = (rng.random(len(acc)) < .02) & ~orphan & ~blank
    acc.loc[lock, 'Locked'] = 'Yes'
    return acc, ae


def _to_bool(s):
    return s.astype(str).str.strip().str.lower().isin(['true', 'yes', 'y', '1', '1.0', 'x'])


def load_data(cfg):
    if cfg.use_demo_data:
        return make_demo_data(cfg.demo_seed)
    path = cfg.input_file
    if not os.path.exists(path) and IN_COLAB:
        print(f"'{path}' not found - please upload your Excel file...")
        up = colab_files.upload()
        if up:
            path = list(up.keys())[0]
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find '{cfg.input_file}'. Upload it or tick 'use_demo_data'.")
    return (pd.read_excel(path, sheet_name=cfg.accounts_sheet),
            pd.read_excel(path, sheet_name=cfg.aes_sheet))


def prepare_data(df_acc, df_ae, cfg):
    """Clean + validate. Returns (acc, ae, data_quality_df). Raises DataError on blocking problems."""
    dq = []

    def add(sev, check, detail='', n=None):
        dq.append({'Severity': sev, 'Check': check, 'Detail': detail, 'Count': n})

    acc, ae = df_acc.copy(), df_ae.copy()
    acc.columns = [str(c).strip() for c in acc.columns]
    ae.columns = [str(c).strip() for c in ae.columns]
    miss_a = [c for c in ACC_REQUIRED if c not in acc.columns]
    miss_e = [c for c in AE_REQUIRED if c not in ae.columns]
    if miss_a:
        add('Error', 'Missing columns in Accounts sheet', ', '.join(miss_a), len(miss_a))
    if miss_e:
        add('Error', 'Missing columns in AE_Roles sheet', ', '.join(miss_e), len(miss_e))
    if acc.empty:
        add('Error', 'Accounts sheet is empty', n=0)
    if ae.empty:
        add('Error', 'AE_Roles sheet is empty', n=0)
    if miss_a or miss_e or acc.empty or ae.empty:
        return acc, ae, pd.DataFrame(dq)

    for c in ['Region', 'Product Focus', 'Status', 'Tier', 'Spend Map', 'Vertical', 'Current AE']:
        acc[c] = acc[c].fillna('').astype(str).str.strip()
    for c in ['AE Name', 'Role', 'Region Alignment']:
        ae[c] = ae[c].fillna('').astype(str).str.strip()
    ae['Is_AI_Specialist'] = _to_bool(ae['Is_AI_Specialist'])

    rev = pd.to_numeric(acc[REV], errors='coerce')
    eff = pd.to_numeric(acc[EFFORT], errors='coerce')
    if rev.isna().sum():
        add('Warning', 'Non-numeric / empty Annual Revenue (treated as 0)', n=int(rev.isna().sum()))
    if eff.isna().sum():
        add('Warning', 'Non-numeric / empty effort hours (treated as 0)', n=int(eff.isna().sum()))
    if (eff < 0).sum():
        add('Warning', 'Negative effort hours (treated as 0)', n=int((eff < 0).sum()))
    acc[REV] = rev.fillna(0).astype(float)
    acc[EFFORT] = eff.fillna(0).clip(lower=0).round().astype(int)

    if 'Account ID' not in acc.columns:
        acc.insert(0, 'Account ID', [f'ACC-{i + 1:05d}' for i in range(len(acc))])
        add('Info', "No 'Account ID' column - IDs were generated", n=len(acc))
    acc['Account ID'] = acc['Account ID'].astype(str).str.strip()
    if (acc['Account ID'] == '').any():
        add('Error', 'Empty Account IDs', n=int((acc['Account ID'] == '').sum()))
    if acc['Account ID'].duplicated().any():
        add('Error', 'Duplicate Account IDs', ', '.join(acc.loc[acc['Account ID'].duplicated(), 'Account ID'].head(5)),
            int(acc['Account ID'].duplicated().sum()))
    if 'Locked' in acc.columns:
        acc['Locked'] = np.where(_to_bool(acc['Locked']), 'Yes', 'No')
    else:
        acc['Locked'] = 'No'
        add('Info', "No optional 'Locked' column - nothing is locked", n=0)

    if ae['AE Name'].duplicated().any():
        add('Error', 'Duplicate AE names (names must be unique)', ', '.join(ae.loc[ae['AE Name'].duplicated(), 'AE Name']),
            int(ae['AE Name'].duplicated().sum()))
    if (ae['AE Name'] == '').any():
        add('Error', 'AEs with empty names', n=int((ae['AE Name'] == '').sum()))
    if (ae['Region Alignment'] == '').any():
        add('Error', 'AEs with empty Region Alignment', n=int((ae['Region Alignment'] == '').sum()))
    if (acc['Region'] == '').any():
        add('Error', 'Accounts with empty Region', n=int((acc['Region'] == '').sum()))
    bad_role = ae[~ae['Role'].isin(KNOWN_ROLES)]
    if len(bad_role):
        add('Warning', f"Unrecognised roles (only global rules apply). Known: {KNOWN_ROLES}",
            ', '.join(sorted(bad_role['Role'].unique())), len(bad_role))
    for col, known in [('Status', ['Customer', 'Prospect']), ('Tier', ['Tier 1', 'Tier 2', 'Tier 3'])]:
        bad = ~acc[col].isin(known)
        if bad.any():
            add('Warning', f"Unexpected '{col}' values (not in {known})", ', '.join(sorted(acc.loc[bad, col].unique())[:5]), int(bad.sum()))

    no_ae = sorted(set(acc['Region']) - set(ae['Region Alignment']))
    if no_ae:
        add('Warning', 'Regions with accounts but no AEs (accounts keep current owner)', ', '.join(no_ae),
            int(acc['Region'].isin(no_ae).sum()))
    ae_region = dict(zip(ae['AE Name'], ae['Region Alignment']))
    owned = acc['Current AE'] != ''
    unknown = owned & ~acc['Current AE'].isin(ae_region)
    if unknown.any():
        add('Warning', 'Current owner not in AE_Roles (left company?) - accounts will be reassigned',
            ', '.join(sorted(acc.loc[unknown, 'Current AE'].unique())[:8]), int(unknown.sum()))
    cross = owned & ~unknown & (acc['Current AE'].map(ae_region) != acc['Region'])
    if cross.any():
        add('Warning', 'Current owner belongs to a different region - treated as unowned in this region', n=int(cross.sum()))
    if (~owned).any():
        add('Info', 'Accounts without a current owner (new) - will be assigned', n=int((~owned).sum()))
    bad_lock = (acc['Locked'] == 'Yes') & (~owned | unknown | cross)
    if bad_lock.any():
        add('Warning', 'Locked accounts without a usable current owner - lock ignored', n=int(bad_lock.sum()))
    if not dq:
        add('Info', 'All data checks passed', n=0)
    dq_df = pd.DataFrame(dq, columns=['Severity', 'Check', 'Detail', 'Count'])
    return acc.reset_index(drop=True), ae.reset_index(drop=True), dq_df


def effort_band(total_effort, n_aes, pct):
    target = int(total_effort / n_aes)
    return target, int(target * (1 - pct / 100)), int(target * (1 + pct / 100))


# ---------------------------------------------------------------------
# 3. MODEL
# ---------------------------------------------------------------------
class RegionData:
    """Plain numpy view of one region - everything the model needs, no pandas inside the loops."""

    def __init__(self, region, acc, ae_all, cfg):
        self.region = region
        a = acc[acc['Region'] == region]
        e = ae_all[ae_all['Region Alignment'] == region]
        self.acc_idx = a.index.to_numpy()
        self.A, self.E = len(a), len(e)
        self.ae_names = e['AE Name'].tolist()
        self.roles = e['Role'].to_numpy()
        self.is_ai = e['Is_AI_Specialist'].to_numpy(bool)
        self.eff = a[EFFORT].to_numpy(int)
        self.rev = a[REV].to_numpy(float)
        self.total_eff = int(self.eff.sum())
        t = a['Tier'].to_numpy()
        s = a['Status'].to_numpy()
        m = a['Spend Map'].to_numpy()
        self.is_t3, self.is_t1 = t == 'Tier 3', t == 'Tier 1'
        self.is_cust, self.is_prosp = s == 'Customer', s == 'Prospect'
        self.is_burn, self.is_growth = m == 'Burn Risk', m == 'Growth Engine'
        self.vert_codes, self.vert_labels = pd.factorize(a['Vertical'])
        self.locked = (a['Locked'] == 'Yes').to_numpy()
        name2j = {n: j for j, n in enumerate(self.ae_names)}
        self.cur_j = np.array([name2j.get(n, -1) for n in a['Current AE']], dtype=int)
        self.n_owned = int((self.cur_j >= 0).sum())
        if self.E:
            self.target, self.lo, self.hi = effort_band(self.total_eff, self.E, cfg.allowed_workload_variance_pct)
        else:
            self.target = self.lo = self.hi = 0
        # eligibility (hard rules that remove variables up-front)
        el = np.ones((self.A, self.E), bool)
        if cfg.ai_specialists_only:
            ai_acc = a['Product Focus'].to_numpy() == 'Enterprise AI'
            el[np.ix_(ai_acc, ~self.is_ai)] = False
        acq = self.roles == 'Enterprise Acquisition'
        bad = self.is_cust | self.is_t1
        el[np.ix_(bad, acq)] = False
        if cfg.respect_locked:
            for i in np.flatnonzero(self.locked & (self.cur_j >= 0)):
                el[i, :] = False
                el[i, self.cur_j[i]] = True
        self.elig = el
        self.n_eligible = el.sum(axis=1)

    def cap_for(self, j, cfg):
        c = cfg.global_max_accounts
        return min(c, cfg.strategic_max_accounts) if self.roles[j] == 'Strategic' else c


def _S(v):
    return cp_model.LinearExpr.Sum(v) if v else 0


def _WS(v, c):
    return cp_model.LinearExpr.WeightedSum(v, c) if v else 0


def build_model(rd, cfg, assume=False):
    """assume=True -> pure feasibility model with one switch per rule family (used for conflict diagnosis)."""
    soft = cfg.constraint_mode == 'soft' and not assume
    m = cp_model.CpModel()
    x, by_ae, by_acc = {}, [[] for _ in range(rd.E)], [[] for _ in range(rd.A)]
    for i in range(rd.A):
        for j in np.flatnonzero(rd.elig[i]):
            x[i, j] = m.NewBoolVar(f'x_{i}_{j}')
            by_ae[j].append(i)
            by_acc[i].append(j)
    lits, slacks, pen = {}, {}, []

    def lit(f):
        if f not in lits:
            lits[f] = m.NewBoolVar('rule::' + f)
        return lits[f]

    def add(f, ct, extra=None):
        conds = ([extra] if extra is not None else []) + ([lit(f)] if assume else [])
        if conds:
            ct.OnlyEnforceIf(conds)
        return ct

    def slack(f, key, ub, w):
        v = m.NewIntVar(0, int(max(ub, 0)) if soft else 0, f's_{f}_{key}')
        slacks.setdefault(f, []).append((key, v))
        pen.append((v, w))
        return v

    # each account -> at most one AE (exactly one when locked or full coverage requested)
    for i in range(rd.A):
        vs = [x[i, j] for j in by_acc[i]]
        if not vs:
            continue
        if cfg.respect_locked and rd.locked[i] and rd.cur_j[i] >= 0:
            m.AddExactlyOne(vs)
        elif cfg.require_full_coverage:
            add('coverage', m.AddExactlyOne(vs))
        else:
            m.AddAtMostOne(vs)

    retained, moved, dev_vars = [], [], []
    retained_by_ae = [[] for _ in range(rd.E)]
    for j in range(rd.E):
        idx = np.array(by_ae[j], dtype=int)
        if idx.size == 0:
            continue
        sel = lambda mask: [x[i, j] for i in idx[mask[idx]]]
        vars_j = [x[i, j] for i in idx]
        eff_j = _WS(vars_j, [int(rd.eff[i]) for i in idx])
        role = rd.roles[j]

        cap = rd.cap_for(j, cfg)
        if len(vars_j) > cap:
            add('cap', m.Add(_S(vars_j) <= cap + slack('cap', j, len(vars_j), cfg.w_cap)))
        t3 = sel(rd.is_t3)
        if cfg.min_tier_3_per_ae > 0:
            add('tier3', m.Add(_S(t3) + slack('tier3', j, cfg.min_tier_3_per_ae, cfg.w_tier3) >= cfg.min_tier_3_per_ae))
        burn = sel(rd.is_burn)
        if len(burn) > cfg.max_burn_risk_per_ae:
            add('burn', m.Add(_S(burn) <= cfg.max_burn_risk_per_ae + slack('burn', j, len(burn), cfg.w_burn)))
        cust, prosp = sel(rd.is_cust), sel(rd.is_prosp)
        if role == 'Strategic' and prosp:
            add('strat', m.Add(_S(cust) + slack('strat', j, len(prosp), cfg.w_strategic_mix) >= _S(prosp)))
        if role == 'Account Manager' and len(prosp) > cfg.am_max_prospects:
            add('amp', m.Add(_S(prosp) <= cfg.am_max_prospects + slack('amp', j, len(prosp), cfg.w_am_prospects)))

        for v in range(len(rd.vert_labels)):
            vv = sel(rd.vert_codes == v)
            if len(vv) > cfg.max_accounts_per_vertical:
                add('vert', m.Add(_S(vv) <= cfg.max_accounts_per_vertical + slack('vert', f'{j}_{v}', len(vv), cfg.w_vertical)))

        # growth-engine rule: if an AE holds "too many" growth engines their workload is capped
        growth = sel(rd.is_growth)
        if len(growth) > cfg.max_growth_engines:
            many = m.NewBoolVar(f'many_growth_{j}')
            m.Add(_S(growth) >= cfg.max_growth_engines + 1).OnlyEnforceIf(many)
            m.Add(_S(growth) <= cfg.max_growth_engines).OnlyEnforceIf(many.Not())
            g_idx = idx[rd.is_growth[idx]]
            lhs = eff_j if cfg.growth_cap_basis == 'total' else _WS([x[i, j] for i in g_idx], [int(rd.eff[i]) for i in g_idx])
            add('grow', m.Add(lhs <= cfg.growth_engine_effort_cap +
                              slack('grow', j, rd.total_eff, cfg.w_growth_cap)), extra=many)

        # workload band (+ soft pull to the exact target)
        add('band_lo', m.Add(eff_j + slack('band_lo', j, rd.lo, cfg.w_effort_band) >= rd.lo))
        if int(rd.eff[idx].sum()) > rd.hi:
            add('band_hi', m.Add(eff_j <= rd.hi + slack('band_hi', j, rd.total_eff, cfg.w_effort_band)))
        if not assume:
            dv = m.NewIntVar(0, max(rd.total_eff, 1), f'dev_{j}')
            m.Add(dv >= eff_j - rd.target)
            m.Add(dv >= rd.target - eff_j)
            dev_vars.append(dv)

        # churn bookkeeping
        in_j = []
        for i in idx:
            c = rd.cur_j[i]
            if c == j:
                retained.append(x[i, j])
                retained_by_ae[j].append(x[i, j])
            elif c >= 0:
                moved.append(x[i, j])
                in_j.append(x[i, j])
        if cfg.max_in_per_ae > 0 and len(in_j) > cfg.max_in_per_ae:
            add('in', m.Add(_S(in_j) <= cfg.max_in_per_ae + slack('in', j, len(in_j), cfg.w_inout)))

    for j in range(rd.E):
        n_own = int((rd.cur_j == j).sum())
        if cfg.max_out_per_ae > 0 and n_own > cfg.max_out_per_ae:
            add('out', m.Add(_S(retained_by_ae[j]) + slack('out', j, n_own, cfg.w_inout) >= n_own - cfg.max_out_per_ae))
    if cfg.max_moves_pct < 100 and moved:
        cap_mv = int(rd.n_owned * cfg.max_moves_pct / 100)
        if len(moved) > cap_mv:
            add('moves', m.Add(_S(moved) <= cap_mv + slack('moves', 'all', len(moved), cfg.w_moves)))

    if not assume:
        val = np.rint(rd.rev / cfg.revenue_unit).astype(int) + cfg.base_value_per_account
        vs, cs = [], []
        for (i, j), v in x.items():
            vs.append(v); cs.append(int(val[i]))
        for v in retained:
            vs.append(v); cs.append(int(cfg.stability_bonus))
        for v, w in pen:
            vs.append(v); cs.append(-int(w))
        for v in dev_vars:
            vs.append(v); cs.append(-int(cfg.balance_weight))
        m.Maximize(_WS(vs, cs))
        for (i, j), v in x.items():
            m.AddHint(v, 1 if rd.cur_j[i] == j else 0)
    else:
        if lits:
            m.AddAssumptions(list(lits.values()))
    return m, {'x': x, 'slacks': slacks, 'lits': lits}


class ProgressCallback(cp_model.CpSolverSolutionCallback):
    def __init__(self, region, verbose=True, every=6.0):
        cp_model.CpSolverSolutionCallback.__init__(self)
        self.region, self.verbose, self.every = region, verbose, every
        self.n, self._last = 0, 0.0

    def on_solution_callback(self):
        self.n += 1
        now = time.time()
        if self.verbose and (self.n == 1 or now - self._last >= self.every):
            self._last = now
            obj, bound = self.ObjectiveValue(), self.BestObjectiveBound()
            gap = abs(bound - obj) / max(1.0, abs(bound)) * 100
            print(f'   [{self.region}] solution #{self.n}: score {obj:,.0f} | gap to best possible {gap:.1f}% | {self.WallTime():.0f}s')


@dataclass
class RegionResult:
    region: str
    status: str
    assigned: np.ndarray
    objective: float = np.nan
    bound: float = np.nan
    gap_pct: float = np.nan
    wall_time: float = 0.0
    n_solutions: int = 0
    conflict: list = field(default_factory=list)
    note: str = ''
    ok: bool = False


def _set_workers(solver, n):
    try:
        solver.parameters.num_workers = n
    except AttributeError:
        solver.parameters.num_search_workers = n


def diagnose_conflict(rd, cfg, seconds=30):
    """Re-solve as a pure-feasibility model with one switch per rule; ask the solver which rules clash."""
    m, ctx = build_model(rd, cfg, assume=True)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = seconds
    _set_workers(s, 1)
    st = s.Solve(m)
    if st != cp_model.INFEASIBLE:
        return None
    core = set(s.SufficientAssumptionsForInfeasibility())
    return [RULE[f] for f, l in ctx['lits'].items() if l.Index() in core]


def solve_region(rd, cfg, workers, verbose=True):
    t0 = time.time()
    if rd.E == 0:
        return RegionResult(rd.region, 'NO_AES', np.full(rd.A, -1), note='No AEs aligned to this region.')
    m, ctx = build_model(rd, cfg)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(cfg.max_time_per_region_seconds)
    solver.parameters.random_seed = int(cfg.random_seed)
    solver.parameters.relative_gap_limit = float(cfg.relative_gap_limit)
    _set_workers(solver, workers)
    cb = ProgressCallback(rd.region, verbose)
    st = solver.Solve(m, cb)
    name = solver.StatusName(st)
    res = RegionResult(rd.region, name, np.full(rd.A, -1), wall_time=time.time() - t0, n_solutions=cb.n)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (i, j), v in ctx['x'].items():
            if solver.BooleanValue(v):
                res.assigned[i] = j
        res.objective, res.bound = solver.ObjectiveValue(), solver.BestObjectiveBound()
        res.gap_pct = abs(res.bound - res.objective) / max(1.0, abs(res.bound)) * 100
        res.ok = True
    elif st == cp_model.INFEASIBLE:
        res.conflict = diagnose_conflict(rd, cfg) or []
        if len(res.conflict) == 1:
            res.note = f'This rule cannot be met with the current data: {res.conflict[0]}. Relax it or switch to soft mode.'
        elif res.conflict:
            res.note = 'These rules cannot all be met together: ' + '; '.join(res.conflict) + '. Relax one of them or switch to soft mode.'
        else:
            res.note = 'Conflict comes from eligibility / locked accounts / full-coverage (not from a tunable rule).'
    else:
        res.note = f'No feasible plan found in {cfg.max_time_per_region_seconds}s - raise the time limit or use soft mode.'
    return res


def preflight(rds, cfg):
    """Cheap arithmetic checks that explain most infeasibility / unassigned accounts before solving."""
    out = []

    def add(region, sev, check, detail):
        out.append({'Region': region, 'Severity': sev, 'Check': check, 'Detail': detail})

    for rd in rds:
        if rd.E == 0:
            add(rd.region, 'Warning', 'No AEs', f'{rd.A} accounts but no AEs aligned to this region.')
            continue
        none = int((rd.n_eligible == 0).sum())
        if none:
            add(rd.region, 'Warning', 'Accounts with no eligible AE',
                f'{none} accounts cannot be given to anyone (AI-specialist rule / Acquisition rule). They will stay unassigned.')
        n_t3 = int(rd.is_t3.sum())
        if n_t3 < rd.E * cfg.min_tier_3_per_ae:
            add(rd.region, 'Warning', 'Tier 3 shortage',
                f'{rd.E} AEs x {cfg.min_tier_3_per_ae} = {rd.E * cfg.min_tier_3_per_ae} Tier 3 accounts needed, only {n_t3} exist.')
        n_burn = int(rd.is_burn.sum())
        if n_burn > rd.E * cfg.max_burn_risk_per_ae:
            add(rd.region, 'Warning', 'Burn Risk overload',
                f'{n_burn} Burn Risk accounts but AEs can hold only {rd.E * cfg.max_burn_risk_per_ae}.')
        cap_total = sum(rd.cap_for(j, cfg) for j in range(rd.E))
        if rd.A > cap_total:
            add(rd.region, 'Warning', 'Not enough account capacity',
                f'{rd.A} accounts vs room for {cap_total}: at least {rd.A - cap_total} accounts will stay unassigned.')
        for v, lab in enumerate(rd.vert_labels):
            n_v = int((rd.vert_codes == v).sum())
            if n_v > rd.E * cfg.max_accounts_per_vertical:
                add(rd.region, 'Warning', f'Vertical "{lab}" overload',
                    f'{n_v} accounts but AEs can hold only {rd.E * cfg.max_accounts_per_vertical}.')
        if rd.lo > cfg.growth_engine_effort_cap and cfg.growth_cap_basis == 'total':
            add(rd.region, 'Warning', 'Growth cap below workload floor',
                f'Workload floor is {rd.lo}h but the growth cap is {cfg.growth_engine_effort_cap}h of TOTAL effort: '
                f'no AE can hold more than {cfg.max_growth_engines} Growth Engines.')
        add(rd.region, 'Info', 'Workload band', f'target {rd.target}h per AE, allowed {rd.lo}h - {rd.hi}h')
    return pd.DataFrame(out, columns=['Region', 'Severity', 'Check', 'Detail'])


# ---------------------------------------------------------------------
# 4. RULE CHECKS (independent of the solver - works for before AND after)
# ---------------------------------------------------------------------
def region_targets(acc, ae, cfg):
    out = {}
    for r, g in acc.groupby('Region'):
        n = int((ae['Region Alignment'] == r).sum())
        if n:
            out[r] = effort_band(int(g[EFFORT].sum()), n, cfg.allowed_workload_variance_pct)
    return out


def ae_stats(acc, owner_col, ae):
    ae_region = dict(zip(ae['AE Name'], ae['Region Alignment']))
    ai_map = dict(zip(ae['AE Name'], ae['Is_AI_Specialist']))
    role_map = dict(zip(ae['AE Name'], ae['Role']))
    df = acc[acc[owner_col].fillna('') != ''].copy()
    df['_n'] = 1
    df['_t3'] = (df['Tier'] == 'Tier 3').astype(int)
    df['_burn'] = (df['Spend Map'] == 'Burn Risk').astype(int)
    df['_grow'] = (df['Spend Map'] == 'Growth Engine').astype(int)
    df['_cust'] = (df['Status'] == 'Customer').astype(int)
    df['_prosp'] = (df['Status'] == 'Prospect').astype(int)
    df['_geff'] = df[EFFORT] * df['_grow']
    cur_ok = df['Current AE'].map(ae_region).eq(df['Region'])
    df['_kept'] = (cur_ok & (df['Current AE'] == df[owner_col])).astype(int)
    df['_in'] = (cur_ok & (df['Current AE'] != df[owner_col])).astype(int)
    ai_ok = df[owner_col].map(ai_map).fillna(False).astype(bool)
    df['_ai_bad'] = ((df['Product Focus'] == 'Enterprise AI') & ~ai_ok).astype(int)
    acq = df[owner_col].map(role_map).eq('Enterprise Acquisition')
    df['_acq_bad'] = (acq & ((df['Status'] == 'Customer') | (df['Tier'] == 'Tier 1'))).astype(int)
    g = df.groupby(owner_col)
    st = pd.DataFrame({
        'accounts': g['_n'].sum(), 'effort': g[EFFORT].sum(), 'revenue': g[REV].sum(),
        'tier3': g['_t3'].sum(), 'burn': g['_burn'].sum(), 'growth': g['_grow'].sum(),
        'growth_effort': g['_geff'].sum(), 'customers': g['_cust'].sum(), 'prospects': g['_prosp'].sum(),
        'kept': g['_kept'].sum(), 'moved_in': g['_in'].sum(), 'ai_bad': g['_ai_bad'].sum(), 'acq_bad': g['_acq_bad'].sum()})
    vm = (df.groupby([owner_col, 'Vertical']).size().reset_index(name='n')
          .sort_values('n', ascending=False).drop_duplicates(owner_col).set_index(owner_col))
    st['top_vertical_n'] = vm['n']
    st['top_vertical'] = vm['Vertical']
    st = st.reindex(ae['AE Name'])
    owned = acc[acc['Current AE'].map(ae_region).eq(acc['Region'])].groupby('Current AE').size()
    st['owned_before'] = owned.reindex(ae['AE Name'])
    num = [c for c in st.columns if c not in ('top_vertical',)]
    st[num] = st[num].fillna(0)
    st['top_vertical'] = st['top_vertical'].fillna('')
    st['moved_out'] = (st['owned_before'] - st['kept']).clip(lower=0)
    return st


def evaluate_rules(acc, owner_col, ae, cfg, targets, label):
    st = ae_stats(acc, owner_col, ae)
    rows = []

    def row(rule, region, who, role, limit, actual, op, note=''):
        breach = max(0, actual - limit) if op == '<=' else max(0, limit - actual)
        rows.append({'State': label, 'Rule': rule, 'Region': region, 'AE': who, 'Role': role, 'Limit': limit,
                     'Actual': actual, 'Operator': op, 'Breach': breach,
                     'Status': 'Breach' if breach > 0 else 'Pass', 'Note': note})

    for _, a in ae.iterrows():
        n, role, reg = a['AE Name'], a['Role'], a['Region Alignment']
        s = st.loc[n]
        cap = min(cfg.global_max_accounts, cfg.strategic_max_accounts) if role == 'Strategic' else cfg.global_max_accounts
        row(RULE['cap'], reg, n, role, cap, s.accounts, '<=')
        if cfg.min_tier_3_per_ae > 0:
            row(RULE['tier3'], reg, n, role, cfg.min_tier_3_per_ae, s.tier3, '>=')
        row(RULE['burn'], reg, n, role, cfg.max_burn_risk_per_ae, s.burn, '<=')
        row(RULE['vert'], reg, n, role, cfg.max_accounts_per_vertical, s.top_vertical_n, '<=', str(s.top_vertical))
        if reg in targets:
            _, lo, hi = targets[reg]
            row(RULE['band_lo'], reg, n, role, lo, s.effort, '>=')
            row(RULE['band_hi'], reg, n, role, hi, s.effort, '<=')
        if s.growth > cfg.max_growth_engines:
            lhs = s.effort if cfg.growth_cap_basis == 'total' else s.growth_effort
            row(RULE['grow'], reg, n, role, cfg.growth_engine_effort_cap, lhs, '<=', f'{int(s.growth)} growth engines')
        if role == 'Strategic':
            row(RULE['strat'], reg, n, role, s.prospects, s.customers, '>=')
        if role == 'Account Manager':
            row(RULE['amp'], reg, n, role, cfg.am_max_prospects, s.prospects, '<=')
        if role == 'Enterprise Acquisition':
            row(RULE['acq'], reg, n, role, 0, s.acq_bad, '<=')
        if cfg.ai_specialists_only:
            row(RULE['ai'], reg, n, role, 0, s.ai_bad, '<=')
        if cfg.max_in_per_ae > 0:
            row(RULE['in'], reg, n, role, cfg.max_in_per_ae, s.moved_in, '<=')
        if cfg.max_out_per_ae > 0:
            row(RULE['out'], reg, n, role, cfg.max_out_per_ae, s.moved_out, '<=')
    if cfg.max_moves_pct < 100:
        for reg in ae['Region Alignment'].unique():
            names = ae.loc[ae['Region Alignment'] == reg, 'AE Name']
            n_own = st.loc[names, 'owned_before'].sum()
            row(RULE['moves'], reg, '(whole region)', '', int(n_own * cfg.max_moves_pct / 100), st.loc[names, 'moved_in'].sum(), '<=')
    if cfg.respect_locked:
        ae_region = dict(zip(ae['AE Name'], ae['Region Alignment']))
        lk = acc[(acc['Locked'] == 'Yes') & acc['Current AE'].map(ae_region).eq(acc['Region'])]
        bad = int((lk['Current AE'] != lk[owner_col].fillna('')).sum())
        row(RULE['lock'], '(all)', '(all)', '', 0, bad, '<=')
    return pd.DataFrame(rows), st


def explain_moves(acc, ae, cfg, before_rules, before_stats, targets):
    """One plain-English reason per account that changed owner."""
    ae_region = dict(zip(ae['AE Name'], ae['Region Alignment']))
    ai_map, role_map = dict(zip(ae['AE Name'], ae['Is_AI_Specialist'])), dict(zip(ae['AE Name'], ae['Role']))
    br = before_rules[before_rules['Status'] == 'Breach'].groupby('AE')['Rule'].apply(lambda s: sorted(set(s)))
    br = br.to_dict()
    reasons = {}
    for i, r in acc[acc['Action'].isin(['Move', 'Reassigned (owner unavailable)'])].iterrows():
        cur, new = r['Current AE'], r['Proposed AE']
        if r['Action'] != 'Move':
            reasons[i] = 'Previous owner is not available in this region (left / other region) - reassigned.'
        elif cfg.ai_specialists_only and r['Product Focus'] == 'Enterprise AI' and not ai_map.get(cur, False):
            reasons[i] = f'Enterprise AI account needs an AI specialist ({cur} is not one).'
        elif role_map.get(cur) == 'Enterprise Acquisition' and (r['Status'] == 'Customer' or r['Tier'] == 'Tier 1'):
            reasons[i] = 'Acquisition AEs cannot hold customers or Tier 1 accounts.'
        elif cur in br:
            reasons[i] = f'Relieves {cur}, who was outside the rules: ' + '; '.join(br[cur][:2]) + ('...' if len(br[cur]) > 2 else '')
        elif ae_region.get(new) in targets and before_stats.loc[new, 'effort'] < targets[ae_region[new]][1]:
            reasons[i] = f'{new} was below the minimum workload; this account brings them into range.'
        else:
            reasons[i] = 'Rebalancing across the region (improves coverage / workload balance).'
    return pd.Series(reasons, dtype=object)


# ---------------------------------------------------------------------
# 5. ORCHESTRATOR
# ---------------------------------------------------------------------
class ReviewResult:
    pass


def run_territory_review(cfg, df_acc=None, df_ae=None, quiet=False):
    cfg.validate()
    t0 = time.time()
    if df_acc is None:
        df_acc, df_ae = load_data(cfg)
    acc, ae, dq = prepare_data(df_acc, df_ae, cfg)
    if (dq['Severity'] == 'Error').any():
        raise DataValidationError(
            'Blocking data problems found. Fix the input data and re-run.', dq
        )
    res = ReviewResult()
    res.cfg, res.run_id = cfg, uuid.uuid4().hex[:8]
    res.timestamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    res.data_quality, res.ae_input = dq, ae
    regions = list(dict.fromkeys(acc['Region']))
    rds = [RegionData(r, acc, ae, cfg) for r in regions]
    res.preflight = preflight(rds, cfg)
    if not quiet:
        print(f'Run {res.run_id}: {len(acc):,} accounts | {len(ae)} AEs | {len(regions)} regions | mode = {cfg.constraint_mode.upper()}')

    cpu = os.cpu_count() or 1
    par = cfg.parallel_regions and len(rds) > 1
    # CP-SAT needs a portfolio of >= 8 workers to use its neighbourhood-search strategies, even on a 2-CPU Colab
    workers = cfg.num_workers or max(8, cpu // min(len(rds), cpu) if par else cpu)

    def work(rd):
        if not quiet and not par:
            print(f'\n== {rd.region}: {rd.A:,} accounts, {rd.E} AEs ==')
        r = solve_region(rd, cfg, workers, verbose=not quiet and not par)
        if not quiet:
            print(f'   -> {rd.region}: {r.status} in {r.wall_time:.0f}s')
        return r

    if par:
        with ThreadPoolExecutor(max_workers=min(len(rds), cpu)) as pool:
            results = list(pool.map(work, rds))
    else:
        results = [work(rd) for rd in rds]

    # ---- assemble assignments ------------------------------------------------
    out = acc.copy()
    out['Proposed AE'] = ''
    out['Solve Status'] = ''
    ae_names_all = set(ae['AE Name'])
    for rd, r in zip(rds, results):
        names = np.array(rd.ae_names + [''], dtype=object)
        prop = names[np.where(r.assigned >= 0, r.assigned, -1)] if rd.E else np.array([''] * rd.A, dtype=object)
        if not r.ok:   # fallback: status quo, so nothing silently disappears
            cur = out.loc[rd.acc_idx, 'Current AE'].to_numpy()
            prop = np.where(np.isin(cur, list(ae_names_all)), cur, '')
        out.loc[rd.acc_idx, 'Proposed AE'] = prop
        out.loc[rd.acc_idx, 'Solve Status'] = r.status if r.ok else f'{r.status} (kept current owner)'
    ae_region = dict(zip(ae['AE Name'], ae['Region Alignment']))
    cur_ok = out['Current AE'].map(ae_region).eq(out['Region'])
    cond = [
        out['Proposed AE'] == '',
        out['Current AE'] == '',
        ~cur_ok,
        out['Proposed AE'] == out['Current AE'],
    ]
    out['Action'] = np.select(cond, ['Unassigned', 'New assignment', 'Reassigned (owner unavailable)', 'Keep'], 'Move')
    out.loc[(out['Action'] == 'Keep') & (out['Locked'] == 'Yes'), 'Action'] = 'Keep (locked)'
    out.loc[(out['Action'] == 'Reassigned (owner unavailable)') & (out['Proposed AE'] == out['Current AE']), 'Action'] = 'Keep'

    targets = region_targets(acc, ae, cfg)
    out['Owner Before'] = np.where(cur_ok, out['Current AE'], '')
    before_rules, before_stats = evaluate_rules(out, 'Owner Before', ae, cfg, targets, 'Before')
    after_rules, after_stats = evaluate_rules(out, 'Proposed AE', ae, cfg, targets, 'After')
    out['Move Reason'] = explain_moves(out, ae, cfg, before_rules, before_stats, targets)
    out['Move Reason'] = out['Move Reason'].fillna('')
    # reasons for unassigned accounts
    elig = {}
    for rd in rds:
        for k, i in enumerate(rd.acc_idx):
            elig[i] = rd.n_eligible[k]
    un = out['Action'] == 'Unassigned'
    out.loc[un, 'Move Reason'] = np.where(pd.Series(out.index[un]).map(elig).to_numpy() == 0,
                                          'No eligible AE (AI-specialist / Acquisition rule).',
                                          'Left out by the optimiser: region capacity or rule limits (lowest-value accounts dropped first).')
    out = out.drop(columns=['Owner Before'])
    res.accounts = out
    res.acc_columns = [c for c in acc.columns]
    res.moves = out[out['Action'].isin(['Move', 'Reassigned (owner unavailable)'])].copy()
    res.unassigned = out[out['Action'] == 'Unassigned'].copy()
    res.rules_before, res.rules_after = before_rules, after_rules
    res.rule_detail = pd.concat([before_rules, after_rules], ignore_index=True)

    # ---- tables for humans ---------------------------------------------------
    rr = res.rule_detail
    sc = rr.groupby(['Rule', 'State']).agg(Checked=('Status', 'size'), Breaches=('Status', lambda s: int((s == 'Breach').sum())),
                                           Units=('Breach', 'sum')).unstack('State')
    sc.columns = [f'{a} ({b})' for a, b in sc.columns]
    sc = sc.reset_index()
    sc['AEs checked'] = sc['Checked (After)']
    sc = sc.rename(columns={'Breaches (Before)': 'Breaches before', 'Breaches (After)': 'Breaches after',
                            'Units (Before)': 'Total breach size before', 'Units (After)': 'Total breach size after'})
    sc['Change'] = sc['Breaches after'] - sc['Breaches before']
    res.scorecard = sc[['Rule', 'AEs checked', 'Breaches before', 'Breaches after', 'Change',
                        'Total breach size before', 'Total breach size after']]

    sm = ae[['Region Alignment', 'AE Name', 'Role', 'Is_AI_Specialist']].rename(columns={'Region Alignment': 'Region'}).copy()
    sm['Accounts before'] = before_stats['accounts'].to_numpy()
    sm['Accounts after'] = after_stats['accounts'].to_numpy()
    sm['Effort before (h)'] = before_stats['effort'].to_numpy()
    sm['Effort after (h)'] = after_stats['effort'].to_numpy()
    sm['Band min (h)'] = sm['Region'].map(lambda r: targets.get(r, (0, 0, 0))[1])
    sm['Band max (h)'] = sm['Region'].map(lambda r: targets.get(r, (0, 0, 0))[2])
    sm['Within band after'] = np.where((sm['Effort after (h)'] >= sm['Band min (h)']) & (sm['Effort after (h)'] <= sm['Band max (h)']), 'Yes', 'No')
    sm['Revenue before'] = before_stats['revenue'].to_numpy()
    sm['Revenue after'] = after_stats['revenue'].to_numpy()
    sm['Tier 3 after'] = after_stats['tier3'].to_numpy()
    sm['Burn Risk after'] = after_stats['burn'].to_numpy()
    sm['Growth Engines after'] = after_stats['growth'].to_numpy()
    sm['Customers after'] = after_stats['customers'].to_numpy()
    sm['Prospects after'] = after_stats['prospects'].to_numpy()
    sm['Kept'] = after_stats['kept'].to_numpy()
    sm['Moved in'] = after_stats['moved_in'].to_numpy()
    sm['Moved out'] = after_stats['moved_out'].to_numpy()
    res.ae_summary = sm
    res.after_stats, res.before_stats, res.targets = after_stats, before_stats, targets

    rs = []
    breach_after = after_rules[after_rules['Status'] == 'Breach'].groupby('Region').size()
    for rd, r in zip(rds, results):
        sub = out.loc[rd.acc_idx]
        rs.append({'Region': rd.region, 'AEs': rd.E, 'Accounts': rd.A, 'Status': r.status,
                   'Plan quality (gap %)': round(r.gap_pct, 2) if r.ok else None,
                   'Seconds': round(r.wall_time, 1), 'Assigned': int((sub['Proposed AE'] != '').sum()),
                   'Moves': int(sub['Action'].isin(['Move', 'Reassigned (owner unavailable)']).sum()),
                   'Rule breaches after': int(breach_after.get(rd.region, 0)),
                   'Note': r.note})
    res.region_summary = pd.DataFrame(rs)
    res.region_results = dict(zip(regions, results))

    owned_valid = int(cur_ok.sum())
    currently_owned = int((out['Current AE'] != '').sum())
    n_moves = int(out['Action'].isin(['Move', 'Reassigned (owner unavailable)']).sum())
    n_assigned = int((out['Proposed AE'] != '').sum())
    b_b, b_a = int((before_rules['Status'] == 'Breach').sum()), int((after_rules['Status'] == 'Breach').sum())
    chk = int(len(before_rules))
    sd_b, sd_a = float(before_stats['effort'].std()), float(after_stats['effort'].std())
    res.kpis = {
        'accounts_total': len(out), 'accounts_assigned': n_assigned, 'coverage_pct': 100 * n_assigned / max(1, len(out)),
        'revenue_covered': float(out.loc[out['Proposed AE'] != '', REV].sum()), 'revenue_total': float(out[REV].sum()),
        'moves': n_moves, 'moves_pct': 100 * n_moves / max(1, currently_owned),
        'retained': int(out['Action'].isin(['Keep', 'Keep (locked)']).sum()),
        'retained_pct': 100 * int(out['Action'].isin(['Keep', 'Keep (locked)']).sum()) / max(1, owned_valid),
        'reassigned': int((out['Action'] == 'Reassigned (owner unavailable)').sum()),
        'new_assigned': int((out['Action'] == 'New assignment').sum()),
        'unassigned': int((out['Action'] == 'Unassigned').sum()),
        'breaches_before': b_b, 'breaches_after': b_a, 'checks': chk,
        'workload_spread_before': sd_b, 'workload_spread_after': sd_a,
        'regions_ok': sum(r.ok for r in results), 'regions_total': len(results),
        'seconds': time.time() - t0}
    return res


# ---------------------------------------------------------------------
# 6. DASHBOARD
# ---------------------------------------------------------------------
def _card(title, value, sub='', color='#1f4e79'):
    return (f"<div style='flex:1;min-width:175px;padding:14px;border-radius:10px;background:#f5f8fc;border:1px solid #dbe5f1;margin:4px'>"
            f"<div style='font-size:12px;color:#5b6b7b'>{title}</div>"
            f"<div style='font-size:26px;font-weight:700;color:{color}'>{value}</div>"
            f"<div style='font-size:12px;color:#5b6b7b'>{sub}</div></div>")


def narrative(res):
    k = res.kpis
    txt = (f"The optimiser reviewed <b>{k['accounts_total']:,}</b> accounts across <b>{k['regions_total']}</b> regions and proposes "
           f"<b>{k['moves']:,}</b> ownership changes (<b>{k['moves_pct']:.1f}%</b> of owned accounts), keeping <b>{k['retained_pct']:.1f}%</b> where they are. ")
    if k['reassigned']:
        txt += f"{k['reassigned']:,} accounts needed a new owner because their previous owner is unavailable. "
    if k['new_assigned']:
        txt += f"{k['new_assigned']:,} new accounts were assigned. "
    txt += (f"Rule breaches fall from <b>{k['breaches_before']}</b> to <b>{k['breaches_after']}</b> out of {k['checks']} checks, and the "
            f"spread in workload between reps goes from {k['workload_spread_before']:,.0f}h to {k['workload_spread_after']:,.0f}h (standard deviation). ")
    if k['unassigned']:
        txt += f"<span style='color:#b45309'><b>{k['unassigned']:,}</b> accounts could not be assigned - see the 'Unassigned' table.</span> "
    if k['regions_ok'] < k['regions_total']:
        txt += "<span style='color:#b91c1c'><b>Some regions failed</b> - those accounts were left with their current owners (see conflict boxes).</span>"
    return txt


def show_dashboard(res, top_flows=40):
    import plotly.express as px
    import plotly.graph_objects as go
    from IPython.display import HTML, display

    k = res.kpis
    ok_color = lambda good: '#15803d' if good else '#b45309'
    cards = ''.join([
        _card('Accounts assigned', f"{k['coverage_pct']:.1f}%", f"{k['accounts_assigned']:,} of {k['accounts_total']:,}", ok_color(k['coverage_pct'] > 99)),
        _card('Stability (kept with owner)', f"{k['retained_pct']:.1f}%", f"{k['retained']:,} kept"),
        _card('Proposed moves', f"{k['moves']:,}", f"{k['moves_pct']:.1f}% of owned accounts"),
        _card('Rule breaches', f"{k['breaches_before']} -> {k['breaches_after']}", f"out of {k['checks']} checks", ok_color(k['breaches_after'] <= k['breaches_before'])),
        _card('Pipeline covered', f"${k['revenue_covered'] / 1e6:,.1f}M", f"of ${k['revenue_total'] / 1e6:,.1f}M"),
        _card('Workload spread (std)', f"{k['workload_spread_after']:,.0f}h", f"was {k['workload_spread_before']:,.0f}h")])
    display(HTML(f"<h2>Territory review - run {res.run_id} ({res.timestamp})</h2>"
                 f"<div style='display:flex;flex-wrap:wrap'>{cards}</div>"
                 f"<p style='font-size:15px;line-height:1.5;max-width:1000px'>{narrative(res)}</p>"))

    for r, rr in res.region_results.items():
        if not rr.ok:
            display(HTML(f"<div style='background:#ffeaea;padding:12px;border-left:5px solid #d9534f;margin:6px 0'>"
                         f"<b>Region {r}: {rr.status}</b><br>{rr.note}<br><i>Accounts in this region keep their current owner.</i></div>"))
    warn = res.preflight[res.preflight['Severity'] == 'Warning']
    if len(warn):
        display(HTML("<h3>Heads-up before you read the results</h3>"))
        display(warn[['Region', 'Check', 'Detail']].style.hide(axis='index'))
    display(HTML('<h3>Region status</h3>'))
    display(res.region_summary.style.hide(axis='index'))
    display(HTML('<h3>Rule scorecard (before vs after)</h3>'))
    display(res.scorecard.style.hide(axis='index'))

    sc = res.scorecard.melt(id_vars='Rule', value_vars=['Breaches before', 'Breaches after'], var_name='When', value_name='AEs in breach')
    px.bar(sc, y='Rule', x='AEs in breach', color='When', barmode='group', orientation='h', template='plotly_white',
           color_discrete_map={'Breaches before': '#d97706', 'Breaches after': '#2563eb'},
           title='How many AEs break each rule').update_layout(height=420, yaxis_title=None).show()

    s = res.ae_summary.sort_values(['Region', 'AE Name'])
    fig = go.Figure()
    fig.add_bar(x=s['AE Name'], y=s['Effort after (h)'], name='After', marker_color='#2563eb')
    fig.add_scatter(x=s['AE Name'], y=s['Effort before (h)'], mode='markers', name='Before',
                    marker=dict(symbol='diamond', size=9, color='#d97706'))
    for col, nm in [('Band min (h)', 'Allowed min'), ('Band max (h)', 'Allowed max')]:
        fig.add_scatter(x=s['AE Name'], y=s[col], mode='markers', name=nm,
                        marker=dict(symbol='line-ew', size=18, line=dict(width=2, color='#16a34a')))
    fig.update_layout(title='Workload per AE (hours) vs allowed band', template='plotly_white', height=450)
    fig.show()

    mv = res.moves[res.moves['Action'] == 'Move']
    if len(mv):
        fl = mv.groupby(['Current AE', 'Proposed AE']).agg(n=('Account ID', 'size'), rev=(REV, 'sum')).reset_index()
        fl = fl.sort_values('n', ascending=False).head(top_flows)
        src, dst = list('from: ' + fl['Current AE']), list('to: ' + fl['Proposed AE'])
        nodes = list(dict.fromkeys(src + dst))
        pos = {n: i for i, n in enumerate(nodes)}
        go.Figure(go.Sankey(node=dict(label=nodes, pad=10, thickness=14),
                            link=dict(source=[pos[a] for a in src], target=[pos[b] for b in dst], value=fl['n'].tolist(),
                                      customdata=fl['rev'].tolist(), hovertemplate='%{value} accounts<br>$%{customdata:,.0f}<extra></extra>'))
                 ).update_layout(title=f'Where accounts move (top {len(fl)} flows)', height=550).show()
    px.histogram(res.accounts[res.accounts['Proposed AE'] != ''], x='Proposed AE', y=REV, color='Spend Map', template='plotly_white',
                 title='Portfolio revenue per AE (after)').update_layout(xaxis={'categoryorder': 'total descending'}).show()

    display(HTML(f"<h3>Proposed moves ({len(res.moves):,}) - first 25</h3>"))
    cols = [c for c in ['Account ID', 'Account Name', 'Region', 'Tier', 'Status', REV, 'Current AE', 'Proposed AE', 'Move Reason'] if c in res.moves]
    display(res.moves[cols].head(25).style.hide(axis='index').format({REV: '${:,.0f}'}))
    if len(res.unassigned):
        display(HTML(f"<h3>Unassigned accounts ({len(res.unassigned):,}) - first 15</h3>"))
        display(res.unassigned[cols].head(15).style.hide(axis='index').format({REV: '${:,.0f}'}))
    bad = res.data_quality[res.data_quality['Severity'].isin(['Warning'])]
    if len(bad):
        display(HTML('<h3>Data quality warnings</h3>'))
        display(bad.style.hide(axis='index'))


# ---------------------------------------------------------------------
# 7. EXCEL EXPORT (with manager review columns)
# ---------------------------------------------------------------------
def export_excel(res, path=None, download=True):
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    path = path or f"{res.cfg.output_prefix}_{dt.datetime.now():%Y%m%d_%H%M}.xlsx"
    k = res.kpis
    summary = pd.DataFrame([
        ('Run ID', res.run_id), ('Run time', res.timestamp), ('Mode', res.cfg.constraint_mode),
        ('Accounts', k['accounts_total']), ('Assigned', k['accounts_assigned']), ('Coverage %', round(k['coverage_pct'], 2)),
        ('Proposed moves', k['moves']), ('Moves % of owned', round(k['moves_pct'], 2)), ('Kept with current owner %', round(k['retained_pct'], 2)),
        ('Reassigned (owner unavailable)', k['reassigned']), ('New accounts assigned', k['new_assigned']), ('Unassigned', k['unassigned']),
        ('Rule breaches before', k['breaches_before']), ('Rule breaches after', k['breaches_after']),
        ('Pipeline covered ($)', round(k['revenue_covered'])), ('Workload std before (h)', round(k['workload_spread_before'])),
        ('Workload std after (h)', round(k['workload_spread_after']))], columns=['Metric', 'Value'])
    moves = res.moves.copy()
    mcols = [c for c in ['Account ID', 'Account Name', 'Region', 'Tier', 'Status', 'Vertical', REV, EFFORT, 'Current AE', 'Proposed AE', 'Action', 'Move Reason'] if c in moves]
    moves = moves[mcols]
    moves['Manager Decision'] = ''
    moves['Lock next cycle'] = ''
    readme = pd.DataFrame({'How to use this workbook': [
        '1. "Run Summary" = headline numbers.  "Rule Scorecard" = which rules improved or still break.',
        '2. "Moves" lists every proposed change with a plain-English reason.',
        '3. Managers: fill "Manager Decision" (Approve / Reject) and optionally "Lock next cycle" (Yes) in the Moves sheet.',
        '4. Blank decision = Approve. Rejected accounts stay with their current owner and are locked in the next run.',
        '5. Upload the reviewed file back to Colab and run the "Build next-cycle input" cell.',
        '6. "Assignments" has every account, "AE Summary" has before/after per rep, "Rule Detail" has every check.',
        '7. "Unassigned" lists accounts nobody could take and why.']})
    sheets = {'README': readme, 'Run Summary': summary, 'Region Status': res.region_summary, 'Moves': moves,
              'Rule Scorecard': res.scorecard, 'AE Summary': res.ae_summary,
              'Assignments': res.accounts, 'Unassigned': res.unassigned[[c for c in mcols if c not in ('Manager Decision', 'Lock next cycle')]],
              'Rule Detail': res.rule_detail, 'Pre-flight': res.preflight, 'Data Quality': res.data_quality,
              'Settings': pd.DataFrame(list(asdict(res.cfg).items()), columns=['Setting', 'Value']),
              'AE_Roles': res.ae_input}
    head_fill = PatternFill('solid', fgColor='1F4E79')
    with pd.ExcelWriter(path, engine='openpyxl') as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
            ws = w.sheets[name]
            ws.freeze_panes = 'A2'
            for c in ws[1]:
                c.font, c.fill, c.alignment = Font(bold=True, color='FFFFFF'), head_fill, Alignment(wrap_text=True, vertical='center')
            for ci, col in enumerate(df.columns, 1):
                sample = df[col].astype(str).head(200).map(len).max() if len(df) else 0
                ws.column_dimensions[ws.cell(1, ci).column_letter].width = int(min(60, max(10, len(str(col)) + 2, (sample or 0) + 2)))
            if 'Status' in df.columns and name in ('Rule Detail',):
                ci = list(df.columns).index('Status') + 1
                for r in range(2, len(df) + 2):
                    cell = ws.cell(r, ci)
                    cell.fill = PatternFill('solid', fgColor='FDE2E2' if cell.value == 'Breach' else 'E3F5E8')
            if name == 'Moves' and len(df):
                ci = list(df.columns).index('Manager Decision') + 1
                dv = DataValidation(type='list', formula1='"Approve,Reject"', allow_blank=True)
                dv2 = DataValidation(type='list', formula1='"Yes,No"', allow_blank=True)
                ws.add_data_validation(dv); ws.add_data_validation(dv2)
                L1, L2 = ws.cell(1, ci).column_letter, ws.cell(1, ci + 1).column_letter
                dv.add(f'{L1}2:{L1}{len(df) + 1}'); dv2.add(f'{L2}2:{L2}{len(df) + 1}')
    print(f'Saved {path}')
    if download and IN_COLAB:
        colab_files.download(path)
    return path


def result_tables(res):
    """Return destination-neutral result tables for Excel, Sheets, or APIs."""
    k = res.kpis
    summary = pd.DataFrame([
        ('Run ID', res.run_id), ('Run time', res.timestamp),
        ('Mode', res.cfg.constraint_mode), ('Accounts', k['accounts_total']),
        ('Assigned', k['accounts_assigned']),
        ('Coverage %', round(k['coverage_pct'], 2)),
        ('Proposed moves', k['moves']),
        ('Moves % of owned', round(k['moves_pct'], 2)),
        ('Kept with current owner %', round(k['retained_pct'], 2)),
        ('Reassigned (owner unavailable)', k['reassigned']),
        ('New accounts assigned', k['new_assigned']),
        ('Unassigned', k['unassigned']),
        ('Rule breaches before', k['breaches_before']),
        ('Rule breaches after', k['breaches_after']),
        ('Pipeline covered ($)', round(k['revenue_covered'])),
        ('Workload std before (h)', round(k['workload_spread_before'])),
        ('Workload std after (h)', round(k['workload_spread_after'])),
    ], columns=['Metric', 'Value'])
    move_columns = [
        c for c in [
            'Account ID', 'Account Name', 'Region', 'Tier', 'Status',
            'Vertical', REV, EFFORT, 'Current AE', 'Proposed AE', 'Action',
            'Move Reason',
        ] if c in res.moves
    ]
    return {
        'Run Summary': summary,
        'Region Status': res.region_summary.copy(),
        'Moves': res.moves[move_columns].copy(),
        'Rule Scorecard': res.scorecard.copy(),
        'AE Summary': res.ae_summary.copy(),
        'Assignments': res.accounts.copy(),
        'Unassigned': res.unassigned.copy(),
        'Rule Detail': res.rule_detail.copy(),
        'Pre-flight': res.preflight.copy(),
        'Data Quality': res.data_quality.copy(),
    }


def build_next_cycle_frames(assignments, reviews, ae_roles, require_complete=True):
    """Build reviewed next-cycle frames without mutating current input tables."""
    required = {
        'Proposal ID', 'Account ID', 'Current AE', 'Proposed AE', 'Action',
        'Manager Decision', 'Lock next cycle',
    }
    missing = sorted(required - set(reviews.columns))
    if missing:
        raise DataValidationError(
            f"Manager Reviews is missing columns: {', '.join(missing)}",
            pd.DataFrame([{
                'Severity': 'Error',
                'Check': 'Manager Reviews columns',
                'Detail': ', '.join(missing),
                'Count': len(missing),
            }]),
        )

    current = reviews.copy()
    current['Manager Decision'] = (
        current['Manager Decision'].fillna('').astype(str).str.strip().str.lower()
    )
    voluntary = current['Action'].eq('Move')
    invalid = voluntary & ~current['Manager Decision'].isin(['approve', 'reject'])
    if require_complete and invalid.any():
        pending = current.loc[invalid, 'Proposal ID'].astype(str).tolist()
        raise DataValidationError(
            f"{len(pending)} voluntary moves are still pending review.",
            pd.DataFrame([{
                'Severity': 'Error',
                'Check': 'Pending manager decisions',
                'Detail': ', '.join(pending[:10]),
                'Count': len(pending),
            }]),
        )

    assignments = assignments.copy().fillna({'Current AE': '', 'Proposed AE': ''})
    by_account = current.drop_duplicates('Account ID', keep='last').set_index('Account ID')
    decision = assignments['Account ID'].map(by_account['Manager Decision']).fillna('')
    action = assignments['Account ID'].map(by_account['Action']).fillna('')
    reject = action.eq('Move') & decision.eq('reject')
    lock_requested = (
        assignments['Account ID']
        .map(by_account['Lock next cycle'].fillna('').astype(str).str.strip().str.lower())
        .eq('yes')
    )
    already_locked = (
        assignments.get('Locked', pd.Series(False, index=assignments.index))
        .astype(str).str.strip().str.lower().isin(['yes', 'true', '1'])
    )
    base_columns = [c for c in assignments.columns if c not in ACC_EXTRA_COLS]
    next_accounts = assignments[base_columns].copy()
    next_accounts['Current AE'] = np.where(
        reject, assignments['Current AE'], assignments['Proposed AE']
    )
    next_accounts['Locked'] = np.where(
        already_locked | reject | lock_requested, 'Yes', 'No'
    )
    return next_accounts, ae_roles.copy()


# ---------------------------------------------------------------------
# 8. REVIEW LOOP & SCENARIOS
# ---------------------------------------------------------------------
def build_next_cycle_input(reviewed_path, out_path='Next_Cycle_Input.xlsx', download=True):
    """Turn a reviewed results workbook into the input file for the next run."""
    a = pd.read_excel(reviewed_path, sheet_name='Assignments')
    mv = pd.read_excel(reviewed_path, sheet_name='Moves')
    roles = pd.read_excel(reviewed_path, sheet_name='AE_Roles')
    a = a.fillna({'Proposed AE': '', 'Current AE': ''})
    dec = mv.set_index('Account ID')[['Manager Decision', 'Lock next cycle', 'Action']].fillna('')
    new_owner = a['Proposed AE'].astype(str).replace('nan', '')
    locked = a['Locked'].astype(str).str.lower().isin(['yes', 'true', '1']) if 'Locked' in a else pd.Series(False, index=a.index)
    rej_all = a['Account ID'].map(dec['Manager Decision'].str.strip().str.lower()).eq('reject')
    is_move = a['Account ID'].map(dec['Action']).eq('Move')
    rej = rej_all & is_move          # a departed owner cannot be "kept", so only real moves can be rejected
    if (rej_all & ~is_move).any():
        print(f'Note: {int((rej_all & ~is_move).sum())} rejections ignored (owner unavailable / new account - they must be assigned).')
    lock_req = a['Account ID'].map(dec['Lock next cycle'].str.strip().str.lower()).eq('yes')
    final = np.where(rej, a['Current AE'], new_owner)
    base_cols = [c for c in a.columns if c not in ACC_EXTRA_COLS]
    nxt = a[base_cols].copy()
    nxt['Current AE'] = final
    nxt['Locked'] = np.where(locked | rej | lock_req, 'Yes', 'No')
    with pd.ExcelWriter(out_path, engine='openpyxl') as w:
        nxt.to_excel(w, sheet_name='Accounts', index=False)
        roles.to_excel(w, sheet_name='AE_Roles', index=False)
    print(f'{int(rej.sum())} rejected moves locked | {int(lock_req.sum())} extra locks | saved {out_path}')
    if download and IN_COLAB:
        colab_files.download(out_path)
    return out_path


def run_scenarios(cfg, scenarios, seconds=30):
    """scenarios = {'Name': {'setting': value, ...}}. Runs each quietly and compares the headline KPIs."""
    import plotly.express as px
    from IPython.display import display
    acc, ae = load_data(cfg)
    rows = []
    for name, over in {'Current settings': {}, **scenarios}.items():
        c = replace(cfg, max_time_per_region_seconds=seconds, **over)
        print(f'Running scenario: {name} ...')
        r = run_territory_review(c, acc, ae, quiet=True)
        k = r.kpis
        rows.append({'Scenario': name, 'Coverage %': round(k['coverage_pct'], 1), 'Moves': k['moves'], 'Kept %': round(k['retained_pct'], 1),
                     'Rule breaches after': k['breaches_after'], 'Unassigned': k['unassigned'],
                     'Workload std (h)': round(k['workload_spread_after']), 'Pipeline covered ($M)': round(k['revenue_covered'] / 1e6, 1)})
    df = pd.DataFrame(rows)
    display(df.style.hide(axis='index'))
    px.bar(df.melt(id_vars='Scenario', value_vars=['Moves', 'Rule breaches after', 'Unassigned']), x='Scenario', y='value',
           color='variable', barmode='group', template='plotly_white', title='Scenario comparison').show()
    return df


def download_input_template(path='Territory_Input_Template.xlsx'):
    acc, ae = make_demo_data(1, aes_per_region=8, accounts_per_ae=4)
    with pd.ExcelWriter(path, engine='openpyxl') as w:
        acc.head(30).to_excel(w, sheet_name='Accounts', index=False)
        ae.to_excel(w, sheet_name='AE_Roles', index=False)
    print(f'Template saved: {path}')
    if IN_COLAB:
        colab_files.download(path)


