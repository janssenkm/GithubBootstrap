# RFC 8785 fixture provenance

`primitive-example.json`, `utf16-order.json`, and `appendix-b.json` transcribe
the examples and number table in RFC 8785 sections 3.2.2, 3.2.3, and Appendix
B: <https://www.rfc-editor.org/rfc/rfc8785.html>. The expected values preserve
the RFC's exact canonical spellings.

`boundary-cases.json` and `duplicate-key.json` are repository-owned boundary
fixtures derived from RFC 8785 sections 3.2.2.2 and 3.2.2.3. They exercise
escaped controls, Unicode, invalid lone surrogates, non-finite values, the
I-JSON integer boundary, and duplicate-key rejection. They are not represented
as additional RFC-published vectors.
