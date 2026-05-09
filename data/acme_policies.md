# ACME SaaS Co — Customer Policy Handbook

**Version:** 3.4  
**Effective Date:** 1 February 2025  
**Owner:** Customer Operations & Legal  
**Review Cycle:** Annual (next review: February 2026)

This handbook defines ACME SaaS Co's binding customer-facing policies for refunds,
billing disputes, cancellations, technical support, escalations, and regulatory
compliance. All Support team members and AI-assisted workflows are required to apply
these policies exactly as written.  Policy identifiers (e.g., ACME 4.2.1) are stable
cross-references used in audit logs, escalation tickets, and Slack approvals.

---

## 1. Definitions

**1.1 Tiers** — Customers are classified into three service tiers:

- **Free** — No contract value. Limited support via documentation and community forum.
  No SLA. No refund entitlement.
- **SMB** (Small and Medium Business) — Annual or monthly subscription, $0–$9,999/yr.
  Standard SLA. Refund rights per §4.
- **Enterprise** — Annual contract $10,000+/yr. Dedicated Customer Success Manager.
  Negotiated SLA. Refund rights per §4 with elevated approval authority.

**1.2 Business Days** — Monday through Friday, 09:00–18:00 US Eastern Time, excluding
US federal holidays and ACME-observed company holidays published annually.

**1.3 Approved Refund Request** — A refund request that has been reviewed by the
appropriate approval authority (see §4.2) and marked "approved" in the customer's
account record.

**1.4 Disputed Charge** — A charge the customer contests in writing within 60 days of
the invoice date.

---

## 2. Scope and Applicability

**2.1** This policy applies to all customers with active or recently-terminated ACME SaaS
subscriptions, including trials that converted to paid plans.

**2.2** Reseller and OEM partners are governed by their separate Reseller Agreement.
This handbook does not apply to partner-managed end users unless the partner's
agreement explicitly incorporates it by reference.

**2.3** In the event of a conflict between this handbook and an individual customer's
Master Subscription Agreement (MSA), the MSA controls.

---

## 3. General Billing Terms

### 3.1 Invoice Cycle

**3.1.1** Monthly subscribers are invoiced on the calendar date matching their
subscription start date (e.g., started March 15 → invoiced the 15th of each month).
If that date falls on a weekend or holiday, the invoice issues the next Business Day.

**3.1.2** Annual subscribers are invoiced on their subscription anniversary date, net-30
unless otherwise specified in their contract.

**3.1.3** All invoices are delivered to the billing email address on file.  It is the
customer's responsibility to maintain an accurate billing email address.

### 3.2 Payment Methods

**3.2.1** ACME accepts Visa, Mastercard, American Express, and ACH bank transfer.
Enterprise customers with $20,000+/yr contracts may request invoice-based billing
(net-30 or net-60) subject to credit approval.

**3.2.2** Declined payments trigger an automatic retry on days +3, +7, and +14. After
three failed attempts, the account is placed in "payment hold" and service access is
restricted pending resolution.

**3.2.3 Resuming service after payment hold:** The customer must resolve the outstanding
balance in full. Partial payments do not lift the hold. Service resumes within
2 Business Days of confirmed payment.

### 3.3 Price Changes

**3.3.1** ACME will provide no less than 60 days written notice of any price increase
to existing subscribers. Price increases take effect at the next renewal date following
the notice period.

**3.3.2** Customers who do not wish to accept a price increase may cancel per §5 without
incurring an early-termination fee, provided cancellation is received before the new
price takes effect.

---

## 4. Refund Policy

> Refund requests must be submitted in writing to support@acmesaas.com or via the
> in-app Help portal. Verbal requests and chat messages are not binding.

### 4.1 Eligibility

**4.1.1** Free-tier customers are not entitled to refunds. There is no charge on the
Free tier, so no refund obligation exists.

**4.1.2** SMB and Enterprise customers may request refunds under the conditions defined
in §4.2. Refunds are not available for partial months of service already rendered,
except in cases of ACME-caused service disruption (§4.3).

**4.1.3** Customers in payment hold are not eligible for new refund requests until the
outstanding balance is resolved.

**4.1.4** Add-on purchases (e.g., premium integrations, one-time professional services
engagements) are non-refundable unless the add-on was materially defective on delivery
and ACME fails to remediate within 10 Business Days.

### 4.2 Refund Approval Authority

Refund requests are tiered by amount. All approvals must be recorded in the CRM with
the approver's name, timestamp, and the policy section cited.

#### 4.2.1 Refunds Under $100

Refunds under $100 may be processed by any Tier-1 Support Agent without manager
sign-off, provided the request meets the eligibility criteria in §4.1 and the
customer's account is in good standing. Processing time: 5 Business Days.

#### 4.2.2 Refunds $100–$500

**Refunds between $100 and $500 require Tier-1 approval plus written sign-off from a
Support Manager before the refund is initiated.** The agent must attach the customer's
original invoice and a brief justification note citing the applicable policy section.
Processing time: 7 Business Days.

#### 4.2.3 Refunds Over $500

**Refunds exceeding $500 require approval from both a Support Manager and the Finance
team (finance-approvals@acmesaas.com).** For Enterprise customers, the account's
dedicated Customer Success Manager must also be notified. Processing time:
10 Business Days. All approvals must be documented in the CRM case before the refund
is issued.

#### 4.2.4 Refunds Over $5,000 (Enterprise only)

For Enterprise accounts, refund requests exceeding $5,000 must be escalated to the VP
of Customer Success for final sign-off. The Support Manager initiates the escalation by
opening a high-priority CRM case tagged "VP-escalation" and notifying the VP by email
within 1 Business Day of receiving the request.

### 4.3 Service Disruption Credits

**4.3.1** If ACME experiences a Sev-1 platform outage (as defined in §8.1) lasting more
than 2 consecutive hours during Business Days, affected SMB and Enterprise customers are
automatically credited 1 day of subscription value per incident, up to a maximum of
5 days per calendar month. Credits appear on the next invoice.

**4.3.2** Proactive credit issuance does not require a customer refund request. If a
customer independently requests a refund for the same incident, the credit already
applied is deducted from any approved refund to avoid double-payment.

**4.3.3** Customers may request additional credits beyond the automatic amount if the
impact was materially greater than the automatic credit reflects. Such requests are
reviewed under §4.2.2 or §4.2.3 depending on the amount.

### 4.4 Chargebacks and Disputes

**4.4.1** Customers who initiate a chargeback without first submitting a formal refund
request to ACME Support will have their account suspended pending chargeback resolution.
ACME reserves the right to provide invoice records, access logs, and usage data to the
payment processor to contest chargebacks.

**4.4.2** Customers with an open chargeback are not eligible for further refund requests
or new contracts until the chargeback is resolved.

---

## 5. Cancellation Policy

### 5.1 Monthly Subscriptions

**5.1.1** Monthly subscribers may cancel at any time. The cancellation takes effect at
the end of the current billing period. No partial-month refunds are issued.

**5.1.2** To cancel, the account owner must submit a written cancellation request through
the in-app Settings > Subscription page or by emailing support@acmesaas.com with the
subject line "Cancellation Request — [Account Name]."

**5.1.3** Cancellations received within 48 hours of the next billing date may be
processed in the following cycle. ACME will not be liable for charges incurred during
this processing window.

### 5.2 Annual Subscriptions

**5.2.1** Annual subscribers who cancel mid-term forfeit the remaining unused months.
No pro-rated refund is issued for early termination unless the cancellation is caused by
ACME's material breach of the subscription agreement (see §5.3).

**5.2.2** Exception — 30-Day New Customer Guarantee: Customers cancelling within 30 days
of their initial annual subscription start date are eligible for a full refund of the
annual fee, regardless of usage. This guarantee applies once per customer entity and
does not apply to renewals.

**5.2.3** Enterprise customers wishing to cancel an annual contract must notify ACME in
writing with 90 days' notice. Early-termination fees may apply per the individual MSA.

### 5.3 Cancellation for Cause

**5.3.1** If ACME materially breaches the subscription agreement (e.g., permanent data
loss, prolonged platform unavailability exceeding 72 consecutive hours), the customer
may cancel immediately with full refund of the unused subscription period.

**5.3.2** "Cancellation for cause" claims must be submitted to legal@acmesaas.com within
30 days of the qualifying incident. ACME has 10 Business Days to investigate and respond.

---

## 6. Billing Disputes

### 6.1 Dispute Window

**6.1.1** Customers must raise billing disputes within 60 days of the invoice date.
Disputes raised after 60 days will not be accepted unless the customer can demonstrate
that the charge was concealed or not disclosed in the invoice.

### 6.2 Dispute Submission

**6.2.1** All disputes must include: the invoice number, the specific line item contested,
the amount in dispute, and a brief explanation of the grounds for the dispute.

**6.2.2** Disputes submitted without a complete invoice number and line item will be
returned to the customer for completion. The 60-day window continues to run during this
period.

### 6.3 Resolution Process

**6.3.1** Simple disputes (invoice calculation errors, wrong seat count billed) must be
resolved within 10 Business Days of a complete submission. If the dispute is upheld, a
corrected invoice or credit note is issued within 5 Business Days of the resolution.

**6.3.2** Complex disputes (contract interpretation, SLA credit calculation, disputed
usage data) require escalation to the Finance team and may take up to 20 Business Days.
The customer is notified of the extended timeline within 3 Business Days of the dispute
being classified as complex.

**6.3.3 Invoice discrepancy rule:** If a customer's billed seat count exceeds their
contracted seat count, ACME will issue a corrected invoice and credit note for the
difference without requiring a formal dispute. Agents should proactively check seat
counts on billing tickets.

### 6.4 Enterprise Billing Customizations

**6.4.1** Enterprise customers may request custom invoice formats (e.g., purchase order
references, local tax ID, split billing to subsidiaries). Custom invoice requests must
be submitted at least 20 Business Days before the invoice date. Requests received after
that window apply to the following invoice cycle.

**6.4.2** Enterprise customers with contracts denominated in non-USD currencies are billed
at the exchange rate published by the ECB on the first Business Day of the invoice month.

---

## 7. Technical Support SLA

### 7.1 Severity Definitions

| Severity | Definition | Examples |
|---|---|---|
| Sev-1 | Platform-wide outage affecting all or most users | Login page down, API returning 500 for all calls |
| Sev-2 | Critical feature broken for a specific customer | SSO failure, bulk import timing out, webhook completely silent |
| Sev-3 | Non-critical feature impaired | Slow load times, single export format broken |
| Sev-4 | Minor issue or how-to question | UI confusion, documentation request |

### 7.2 Response Time SLA

| Tier | Sev-1 | Sev-2 | Sev-3 | Sev-4 |
|---|---|---|---|---|
| Free | Community forum only | Community forum only | Community forum only | Community forum only |
| SMB | 4 Business Hours | 1 Business Day | 3 Business Days | 5 Business Days |
| Enterprise | 1 Business Hour | 4 Business Hours | 1 Business Day | 3 Business Days |

**7.2.1** "Response time" means time to first substantive response from a support agent,
not time to resolution. Resolution time targets are in §7.3.

**7.2.2** SLA timers begin when the ticket is received and confirmed by ACME's support
system, not when the customer believes they submitted it. Customers should retain their
ticket confirmation number as proof of submission time.

### 7.3 Resolution Time Targets (non-binding, best-effort)

| Tier | Sev-1 | Sev-2 | Sev-3 |
|---|---|---|---|
| SMB | 8 Business Hours | 5 Business Days | 10 Business Days |
| Enterprise | 4 Business Hours | 2 Business Days | 5 Business Days |

**7.3.1** Resolution targets are best-effort only and do not trigger credits unless a
separate SLA addendum signed by both parties specifies otherwise.

### 7.4 SLA Credits for Missed Response Times

**7.4.1** If ACME misses the Sev-1 or Sev-2 response time SLA for an SMB or Enterprise
customer, the customer is entitled to request a credit equal to one day of subscription
value per breach. Credits require a written request within 14 days of the breach.

**7.4.2** SLA credit requests are processed under the refund approval thresholds in §4.2.

---

## 8. Platform Availability

### 8.1 Uptime Commitment

**8.1.1** ACME targets 99.9% monthly uptime for the production platform, measured as
((total minutes − downtime minutes) / total minutes × 100). Downtime is defined as the
platform being completely inaccessible for more than 5 consecutive minutes.

**8.1.2** Scheduled maintenance windows (announced at least 72 hours in advance on the
ACME Status Page) do not count as downtime.

**8.1.3** Outages caused by customer misconfiguration, third-party infrastructure (e.g.,
AWS us-east-1 region incidents), or force majeure events do not count toward ACME's
downtime calculation.

### 8.2 Status Page

**8.2.1** Real-time platform status is published at status.acmesaas.com. Customers are
encouraged to subscribe to status updates for their region.

---

## 9. Data, Privacy, and Compliance

### 9.1 Data Retention

**9.1.1** Customer data is retained for 90 days following account cancellation. After
90 days, data is deleted from production systems. Backup purge completes within 30
additional days (120 days total post-cancellation).

**9.1.2** Customers who require data export before deletion must submit a request through
the in-app Data Export tool or to support@acmesaas.com within 60 days of cancellation.
ACME will fulfill the export request within 10 Business Days.

### 9.2 GDPR and Data Residency

**9.2.1** For customers subject to the EU General Data Protection Regulation, ACME acts
as a Data Processor under the terms of ACME's Data Processing Agreement (DPA), available
at legal.acmesaas.com/dpa. EU data residency (data stored exclusively in AWS eu-west-1)
is available to Enterprise tier customers upon written request.

**9.2.2** Data subject access requests (DSARs) submitted by end users must be forwarded
to ACME's Privacy team at privacy@acmesaas.com within 3 Business Days of receipt.
ACME will respond within 30 calendar days as required by GDPR Article 12.

**9.2.3** Customers who require a signed DPA must request one from legal@acmesaas.com.
Unsigned DPAs do not bind ACME.

### 9.3 Security Incidents

**9.3.1** In the event of a confirmed security incident affecting customer data, ACME
will notify affected customers within 72 hours of discovery, as required under GDPR
Article 33 / applicable breach notification laws.

**9.3.2** Security incident notifications are sent to the security contact email on file.
Customers are responsible for maintaining an up-to-date security contact.

### 9.4 Account Recovery

**9.4.1** Requests to recover access to an account after administrative credential loss
must be submitted by the original account owner with government-issued photo ID.
ACME Support will not recover accounts based solely on email address or payment method
verification — these are insufficient for identity confirmation.

**9.4.2 Account recovery for Enterprise customers** must additionally be confirmed by
the organization's HR or legal representative to protect against insider-threat misuse.
Recovery requests are processed within 3 Business Days.

---

## 10. Escalation Procedures

### 10.1 Escalation Triggers (mandatory escalation to senior staff)

Support Agents must immediately escalate the following ticket types to a Support Manager.
Failure to escalate on these trigger categories is a policy violation:

1. Any customer threatening legal action or referencing their legal counsel.
2. Any refund request exceeding $500 (per §4.2.3).
3. Any allegation of data breach or unauthorized data access.
4. Any Enterprise customer with an open ticket unresolved beyond their Sev-2 SLA window.
5. Any customer disputing an auto-renewal charge who claims they submitted a cancellation.
6. Any account with more than 3 open high-priority tickets simultaneously.
7. Any request that references regulatory bodies (FTC, ICO, CNIL, BaFin, etc.).

### 10.2 Escalation Process

**10.2.1** Agents must document the escalation trigger, the customer tier, the ticket ID,
and the approximate dollar amount at risk in the escalation note.

**10.2.2** The escalating agent retains ownership of the ticket until the Support Manager
explicitly accepts handover. "I escalated it" is not a complete resolution.

**10.2.3** Escalated tickets must receive a manager acknowledgment within:
  - SMB: 2 Business Hours
  - Enterprise: 30 minutes

### 10.3 Legal and Compliance Escalations

**10.3.1** Tickets involving legal threats, regulatory inquiries, or potential GDPR
violations must be forwarded to legal@acmesaas.com simultaneously with the Support
Manager escalation. Legal has final authority on response content for these tickets.

**10.3.2** No commitments, admissions, or settlement offers may be made in a customer-
facing message after legal escalation is triggered, without written approval from Legal.

---

## 11. Agent Authority Matrix

The following table defines what Support Agents are authorized to do without managerial
approval. Any action not listed requires escalation.

| Action | Tier-1 Agent | Support Manager | Finance/VP |
|---|---|---|---|
| Process refund < $100 | YES | YES | YES |
| Process refund $100–$500 | NO (initiate only) | YES | YES |
| Process refund > $500 | NO | NO (initiate only) | YES |
| Issue SLA credit (1 day) | YES | YES | YES |
| Waive late payment fee (1x) | NO | YES | YES |
| Extend free trial by 7 days | YES | YES | YES |
| Approve mid-term cancellation refund | NO | NO | YES |
| Authorize data export after 90-day window | NO | YES | YES |
| Confirm account recovery (Free/SMB) | YES (with ID) | YES | YES |
| Confirm account recovery (Enterprise) | NO | YES (with HR) | YES |

---

## 12. Policy Amendments

**12.1** ACME reserves the right to amend this policy at any time. Customers will be
notified of material changes at least 30 days in advance via email to the account
owner's registered address.

**12.2** Continued use of ACME's platform after the effective date of a policy change
constitutes acceptance of the revised policy.

**12.3** Policy questions should be directed to support@acmesaas.com or, for legal
interpretations, to legal@acmesaas.com.

---

*End of ACME SaaS Co Customer Policy Handbook v3.4*
