/*
  mart_risk_scores.sql
  Migrated from: Programs/Banking/credit_risk_scoring.sas

  SAS Original:
    DATA step applying logistic regression scorecard with WOE binning,
    computing PD/LGD/EAD, and assigning risk ratings

  dbt Equivalent:
    SQL CASE expressions replace SAS IF/THEN WOE assignment
    Mathematical functions replace SAS exp() and computed fields
    This is one of the more complex migrations: SAS DATA step
    business logic maps to a SQL model with nested CASE expressions
*/

with score_input as (
    select
        a.account_id,
        a.customer_id,
        a.account_type,
        a.current_balance,
        a.credit_limit,
        a.acct_age_months,
        a.days_inactive,
        a.utilization_pct,
        a.customer_segment,
        a.region_code,
        b.fico_score,
        b.bureau_inqs_6mo,
        b.bureau_derogs,
        p.pmt_late_90_12mo,
        p.max_days_past_due_ever,
        c.collateral_value,
        case
            when c.collateral_value > 0
            then a.current_balance / c.collateral_value
            else null
        end as ltv
    from {{ ref('int_account_metrics') }} a
    left join {{ source('banking_raw', 'bureau_scores') }} b
        on a.customer_id = b.customer_id
    left join {{ source('banking_raw', 'payment_history') }} p
        on a.account_id = p.account_id
    left join {{ source('banking_raw', 'collateral') }} c
        on a.account_id = c.account_id
    where a.account_type in ('MTG','AUTO','PERS','CC','LOC','HELC')
),

-- SAS: WOE (Weight of Evidence) binning from DATA step
woe_scored as (
    select
        *,

        -- SAS: WOE_FICO variable
        case
            when fico_score >= 760 then -1.204
            when fico_score >= 720 then -0.812
            when fico_score >= 680 then -0.356
            when fico_score >= 640 then  0.198
            when fico_score >= 600 then  0.654
            when fico_score is not null then 1.102
            else 0.198
        end as woe_fico,

        -- SAS: WOE_UTIL variable
        case
            when utilization_pct <= 10 then -0.956
            when utilization_pct <= 30 then -0.521
            when utilization_pct <= 50 then -0.102
            when utilization_pct <= 70 then  0.334
            when utilization_pct <= 90 then  0.789
            when utilization_pct is not null then 1.245
            else 0
        end as woe_util,

        -- SAS: WOE_DPD variable
        case
            when pmt_late_90_12mo = 0 then -0.678
            when pmt_late_90_12mo = 1 then  0.445
            when pmt_late_90_12mo is not null then 1.567
            else 0
        end as woe_dpd,

        -- SAS: WOE_AGE variable
        case
            when acct_age_months >= 120 then -0.534
            when acct_age_months >= 60  then -0.289
            when acct_age_months >= 24  then  0.045
            when acct_age_months is not null then 0.456
            else 0
        end as woe_age,

        -- SAS: WOE_LTV variable (secured only)
        case
            when account_type not in ('MTG','AUTO','HELC') then 0
            when ltv <= 0.60 then -0.712
            when ltv <= 0.80 then -0.234
            when ltv <= 1.00 then  0.356
            when ltv is not null then 0.889
            else 0
        end as woe_ltv

    from score_input
),

-- SAS: LOG_ODDS and PD calculation
pd_calc as (
    select
        *,
        -- SAS: LOG_ODDS = INTERCEPT + weighted WOE
        (-3.2145
            + 0.412 * woe_fico
            + 0.198 * woe_util
            + 0.289 * woe_dpd
            + 0.067 * woe_age
            + 0.134 * woe_ltv
        ) as log_odds,

        -- SAS: PD = 1 / (1 + exp(-LOG_ODDS))
        1.0 / (1.0 + exp(-(-3.2145
            + 0.412 * woe_fico
            + 0.198 * woe_util
            + 0.289 * woe_dpd
            + 0.067 * woe_age
            + 0.134 * woe_ltv
        ))) as pd,

        -- SAS: LGD estimation
        case
            when account_type in ('MTG','AUTO','HELC') and ltv is not null
                then greatest(0, least(1, (ltv - 0.5) * 0.8))
            when account_type in ('MTG','AUTO','HELC')
                then 0.40
            when account_type = 'CC' then 0.75
            else 0.50
        end as lgd,

        -- SAS: EAD estimation
        case
            when account_type in ('CC','LOC','HELC')
                then current_balance + 0.50 * (credit_limit - current_balance)
            else current_balance
        end as ead

    from woe_scored
)

select
    account_id,
    customer_id,
    account_type,
    current_balance,
    fico_score,
    utilization_pct,
    acct_age_months,
    ltv,
    pd,
    lgd,
    ead,
    pd * lgd * ead as expected_loss,

    -- SAS: Risk Rating assignment
    case
        when pd < 0.005 then 1
        when pd < 0.01  then 2
        when pd < 0.03  then 3
        when pd < 0.07  then 4
        when pd < 0.15  then 5
        when pd < 0.30  then 6
        else 7
    end as risk_rating,

    current_date() as score_date,
    'CRM-2023-Q4-v2' as model_id,
    current_timestamp() as score_timestamp

from pd_calc
