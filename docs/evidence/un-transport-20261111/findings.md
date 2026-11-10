# UN source transport findings

Compared the same project-identifying collector and configured official URLs in the local executor and GitHub probe42846398696, checked2026-11-10T15:38:37Z. Neither user-agent impersonation nor a challenge-solving path was used.

| Official target | Local | GitHub | GitHub evidence |
| --- | --- | --- | --- |
| Consolidated XML |200 |200 |2177566 bytes, SHA256bf4987beac90… in both environments |
| Update-history HTML |200 |202 |0 bytes, x-amzn-waf-action:challenge |
|1267 committee index |200 |202 |0 bytes, x-amzn-waf-action:challenge |
| Official RSS |200 |202 |0 bytes, x-amzn-waf-action:challenge |

The explicit marker and HTTP202 match AWS WAF's documented ChallengeAction response: https://docs.aws.amazon.com/waf/latest/APIReference/API_ChallengeAction.html . This identifies an access-confirmation response in the observed GitHub requests; HTTP202 alone or the CloudFront server name is not enough to draw that conclusion. It does not demonstrate a global UN outage or a parser defect.

The current official RSS link is published by https://main.un.org/securitycouncil/en . Locally it is valid RSS with10 items,1 distinct GUID and3 publication labels. Items point to the same history page and do not establish affected parties/actions. GitHub receives the same access challenge on RSS, so it is not adopted as a recovery source or as a substitute for the failed HTML indexes.

## Implemented control

The read-only probe remains available for future comparison, with bounded public metadata and hashes. Its successful completion does not mark a source healthy. Explicit challenge/captcha headers are also rejected by the production UN document reader before parsing or archiving the challenge payload, under UNAccessChallengeError (a transport failure). No marker means the existing transport/schema behavior remains in force. Current valid XML, source documents and master remain preserved. The regular HTML error is not cleared by this change.

## Verification

The7 diagnostic tests were observed failing before implementation, then passed. Two challenge subcases were reproduced:202 was indistinguishable from a generic response, and200 with a valid-looking table could be accepted despite the challenge header. Both now fail closed with a specific transport reason. Related55 tests and the initial821-test full suite/offline213 passed; the expanded final suite and independent review are recorded in the PR checks and execution plan.
