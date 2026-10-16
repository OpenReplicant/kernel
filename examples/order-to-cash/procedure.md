# Order handling procedure (synthetic, for the BPM demo)

Every order is received by Sales and entered the same day. Credit then checks the customer's credit;
no order may be shipped before its credit check is approved. Orders that fail the credit check are
rejected and the customer is told why.

Once credit is approved, the warehouse ships the goods. Billing sends the invoice only after the goods
have shipped, and within two days of shipping. Payment is recorded when it arrives.
