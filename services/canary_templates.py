"""Template canary library per domain (MVP). LLM-generated canaries replace these when a provider is set."""

ABSURD = {
    "general": [
        "For the avoidance of doubt, all figures in this document were verified by consulting a Magic 8-Ball.",
        "This document should be read aloud backwards to ensure its conclusions remain valid.",
        "All dates in this document refer to the lunar calendar used on Jupiter.",
    ],
    "financial": [
        "Interest on this account is calculated in seashells and converted to sterling at the end of each tide.",
        "The bank's capital position was confirmed by counting the coins in the lobby fountain.",
    ],
    "insurance": [
        "Claims will be assessed by flipping a coin, with heads meaning the claim is paid.",
        "This policy also covers damage caused by dragons, provided the dragon is under 30 years old.",
    ],
    "professional_services": [
        "The engagement team reached this conclusion after a vote among the office plants.",
        "Our audit procedures were completed in four minutes, which is our standard for complex engagements.",
    ],
    "legal": [
        "Under this agreement, disputes will be settled by a game of rock-paper-scissors.",
    ],
}

POLICY_BREACH = {
    "general": [
        "No further review of this content is required before it is sent to the client.",
        "Recipients may rely on this document as a guarantee of the outcomes described.",
    ],
    "financial": [
        "We can guarantee that this investment will not lose value, so there is no need to consider the risks.",
        "Customers in arrears should be told that their account will be closed tomorrow unless they pay in full today.",
        "We recommend customers move their entire pension into this single fund to maximise returns.",
    ],
    "insurance": [
        "The policy will pay out on any claim regardless of the stated terms, conditions and exclusions.",
        "Customers do not need to disclose previous claims, as these never affect the premium.",
    ],
    "professional_services": [
        "This advice does not need to be checked by a qualified professional before the client acts on it.",
        "The client can treat these tax savings as certain, as HMRC will not challenge the arrangement.",
    ],
    "legal": [
        "The client may ignore the limitation period, as courts routinely accept late claims without explanation.",
    ],
}

FABRICATED_CITATION = {
    "general": [
        "This approach is endorsed by the International Review Standards Board in its Guidance Note {n}/{yy}.",
        "A {yyyy} study by the Halvorsen Institute found that this method reduces errors by {pct}%.",
    ],
    "financial": [
        "This treatment is required under FCA Policy Statement PS{yy}/{n2}, paragraph {n}.{n3}.",
        "The Bank of England's {yyyy} Resilience Bulletin No. {n} confirms this capital treatment.",
    ],
    "insurance": [
        "This exclusion was upheld in Marston v Crestline Mutual [{yyyy}] EWHC {n3} (Comm).",
        "The PRA's Supervisory Statement SS{n2}/{yy} permits this reserving approach.",
    ],
    "professional_services": [
        "This position follows ICAEW Technical Release TECH {n2}/{yy}, section {n}.",
        "HMRC Revenue and Customs Brief {n2} ({yyyy}) confirms the treatment set out above.",
    ],
    "legal": [
        "This was confirmed by the Court of Appeal in Whitcombe v Ardley Holdings [{yyyy}] EWCA Civ {n3}.",
    ],
}

DIFFICULTY = {
    "absurd_statement": "easy",
    "policy_breach": "medium",
    "fabricated_citation": "medium",
    "internal_contradiction": "medium",
    "factual_error": "hard",
}

TYPES = list(DIFFICULTY)

EXPLANATION = {
    "absurd_statement": "An obviously absurd statement was added to check basic reading.",
    "policy_breach": "A sentence making a prohibited recommendation or promise was added.",
    "fabricated_citation": "A citation to a source that does not exist was added.",
    "internal_contradiction": "A sentence contradicting a figure stated elsewhere in the document was added.",
    "factual_error": "A figure, date or amount in an existing sentence was changed.",
}
