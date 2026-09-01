"""Shared visual styling for TripSync workspaces."""

from __future__ import annotations

import streamlit as st


def apply_styles(workspace: str = "Plan a trip") -> None:
    workspace_colors = {
        "Plan a trip": {
            "accent": "#D7897F",
            "primary": "#8C4640",
            "soft": "#F8E4DF",
            "glow": "rgba(215,137,127,0.24)",
        },
        "My trips": {
            "accent": "#F9B95C",
            "primary": "#8A5E17",
            "soft": "#FDEDD2",
            "glow": "rgba(249,185,92,0.24)",
        },
        "Feedback insights": {
            "accent": "#96C7B3",
            "primary": "#2F5A49",
            "soft": "#E4F1EC",
            "glow": "rgba(150,199,179,0.28)",
        },
        "Curate catalog": {
            "accent": "#6398A9",
            "primary": "#1F4552",
            "soft": "#E4EFF2",
            "glow": "rgba(99,152,169,0.25)",
        },
    }
    page_colors = workspace_colors.get(
        workspace,
        workspace_colors["Plan a trip"],
    )
    st.markdown(
        f"""
        <style>
        :root {{
            --page-accent: {page_colors["accent"]};
            --page-primary: {page_colors["primary"]};
            --page-soft: {page_colors["soft"]};
            --page-glow: {page_colors["glow"]};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <style>
        :root {
            --nectarine: #D7897F;
            --nectarine-dark: #8C4640;
            --peach: #F9B95C;
            --peach-dark: #8A5E17;
            --mint: #96C7B3;
            --mint-dark: #2F5A49;
            --lagoon: #6398A9;
            --lagoon-dark: #1F4552;
            --cream: #FDF6EF;
            --ink: #3A2E2B;
            --muted: #8A7A75;
            --paper: #FFFFFF;
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 8% 14%, var(--page-glow), transparent 22rem),
                var(--cream);
            transition: background 220ms ease;
        }

        [data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            max-width: 1180px;
            padding-top: 1rem;
            padding-bottom: 5.5rem;
        }

        .st-key-workspace-nav {
            border-bottom: 1px solid var(--page-glow);
            isolation: isolate;
            margin-bottom: 1.7rem;
            padding: 0.35rem 0 0.9rem;
            position: relative;
            z-index: 20;
        }

        .ts-nav-brand {
            color: var(--nectarine-dark);
            font-family: "Baloo 2", sans-serif;
            font-size: 1.65rem;
            font-weight: 800;
            letter-spacing: -0.04em;
            line-height: 1;
        }

        .ts-nav-brand span {
            color: var(--lagoon);
        }

        .st-key-workspace-nav button {
            pointer-events: auto !important;
            position: relative;
            z-index: 21;
        }

        .st-key-workspace-nav button[data-testid="stBaseButton-tertiary"] {
            color: var(--ink);
        }

        .st-key-workspace-nav button[data-testid="stBaseButton-tertiary"]:hover {
            background: var(--page-soft);
        }

        .st-key-workspace-nav button[data-testid="stBaseButton-primary"],
        .st-key-workspace-page button[data-testid^="stBaseButton-primary"] {
            background: var(--page-primary);
            border-color: var(--page-primary);
            box-shadow: 0 8px 20px var(--page-glow);
        }

        .st-key-workspace-page button[data-testid="stBaseButton-secondary"] {
            background: rgba(255,255,255,0.72);
            border-color: var(--page-accent);
            color: var(--page-primary);
        }

        .st-key-workspace-page button[data-testid="stBaseButton-secondary"]:hover {
            background: var(--page-soft);
            border-color: var(--page-primary);
        }

        .st-key-workspace-page [data-testid="stSlider"]
        [data-rac][data-orientation="horizontal"] > div[data-rac]
        > div[data-rac] {
            background-color: var(--page-primary);
        }

        .st-key-workspace-plan-a-trip button[data-testid="stBaseButton-tertiary"] {
            color: var(--nectarine-dark);
        }

        .st-key-workspace-my-trips button[data-testid="stBaseButton-tertiary"] {
            color: var(--peach-dark);
        }

        .st-key-workspace-feedback-insights button[data-testid="stBaseButton-tertiary"] {
            color: var(--mint-dark);
        }

        .st-key-workspace-curate-catalog button[data-testid="stBaseButton-tertiary"] {
            color: var(--lagoon-dark);
        }

        .st-key-workspace-plan-a-trip button p::before,
        .st-key-workspace-my-trips button p::before,
        .st-key-workspace-feedback-insights button p::before,
        .st-key-workspace-curate-catalog button p::before {
            border-radius: 50%;
            content: "";
            display: inline-block;
            height: 0.48rem;
            margin-right: 0.42rem;
            vertical-align: 0.04rem;
            width: 0.48rem;
        }

        .st-key-workspace-plan-a-trip button p::before {
            background: var(--nectarine);
        }

        .st-key-workspace-my-trips button p::before {
            background: var(--peach);
        }

        .st-key-workspace-feedback-insights button p::before {
            background: var(--mint);
        }

        .st-key-workspace-curate-catalog button p::before {
            background: var(--lagoon);
        }

        .st-key-workspace-page {
            background: linear-gradient(
                180deg,
                var(--page-soft) 0,
                rgba(255,255,255,0.34) 25rem,
                rgba(255,255,255,0) 48rem
            );
            border-top: 6px solid var(--page-accent);
            border-radius: 1.8rem;
            box-shadow: 0 18px 48px var(--page-glow);
            padding: 1.45rem 1.5rem 2.25rem;
            transition: background 220ms ease, border-color 220ms ease;
        }

        .st-key-workspace-page h1,
        .st-key-workspace-page .ts-section-label,
        .st-key-workspace-page .ts-score {
            color: var(--page-primary);
        }

        .st-key-workspace-page div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--page-glow);
        }

        .st-key-hero {
            margin-bottom: 1.4rem;
            padding: 0.8rem 0 1rem;
            position: relative;
        }

        .st-key-hero-copy {
            padding: 1.2rem 1.6rem 1rem 0.15rem;
        }

        .ts-brand {
            color: var(--nectarine);
            font-size: 0.76rem;
            font-weight: 800;
            letter-spacing: 0.14em;
            margin-bottom: 0.65rem;
            text-transform: uppercase;
        }

        .st-key-hero h1 {
            color: var(--nectarine-dark);
            font-family: "Baloo 2", sans-serif;
            font-size: clamp(2.8rem, 5.3vw, 4.5rem);
            font-weight: 800;
            letter-spacing: -0.045em;
            line-height: 0.98;
            margin: 0 0 1rem;
            max-width: 38rem;
        }

        .ts-kicker {
            color: var(--muted);
            font-size: 1.02rem;
            line-height: 1.65;
            margin: 0;
            max-width: 34rem;
        }

        .ts-hero-visual {
            background: linear-gradient(155deg, var(--peach) 0%, var(--nectarine) 100%);
            border-radius: 1.8rem;
            box-shadow: 0 20px 50px rgba(140,70,64,0.16);
            height: 22rem;
            overflow: hidden;
            position: relative;
        }

        .ts-hero-arch {
            background: var(--mint);
            border-radius: 10rem 10rem 0 0;
            bottom: 0;
            height: 82%;
            left: 50%;
            position: absolute;
            transform: translateX(-50%);
            width: 68%;
        }

        .ts-hero-route {
            border: 2px dashed rgba(47,90,73,0.40);
            border-bottom: 0;
            border-radius: 8rem 8rem 0 0;
            bottom: 0;
            height: 62%;
            left: 50%;
            position: absolute;
            transform: translateX(-50%);
            width: 44%;
        }

        .ts-hero-route::before,
        .ts-hero-route::after {
            background: var(--lagoon-dark);
            border: 4px solid rgba(255,255,255,0.92);
            border-radius: 50%;
            content: "";
            height: 1rem;
            position: absolute;
            width: 1rem;
        }

        .ts-hero-route::before {
            left: -0.55rem;
            top: 58%;
        }

        .ts-hero-route::after {
            right: -0.55rem;
            top: 10%;
        }

        .ts-visual-badge {
            background: rgba(255,255,255,0.96);
            border: 1px solid rgba(58,46,43,0.05);
            border-radius: 1rem;
            box-shadow: 0 8px 22px rgba(58,46,43,0.12);
            color: var(--lagoon-dark);
            font-size: 0.82rem;
            font-weight: 800;
            line-height: 1.25;
            padding: 0.72rem 0.9rem;
            position: absolute;
        }

        .ts-visual-badge small {
            color: var(--muted);
            display: block;
            font-size: 0.67rem;
            font-weight: 600;
            margin-top: 0.18rem;
        }

        .ts-visual-badge--top {
            left: 1.35rem;
            top: 1.35rem;
        }

        .ts-visual-badge--bottom {
            bottom: 1.35rem;
            right: 1.35rem;
        }

        .ts-hero-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 1.35rem;
        }

        .ts-hero-badge {
            align-items: center;
            background: var(--paper);
            border: 1px solid #ECDDD3;
            border-radius: 999px;
            box-shadow: 0 5px 14px rgba(140,70,64,0.05);
            color: var(--nectarine-dark);
            display: inline-flex;
            font-size: 0.74rem;
            font-weight: 800;
            padding: 0.42rem 0.72rem;
        }

        .st-key-progress-card {
            background: rgba(255,255,255,0.78);
            border: 1px solid #EADBD2;
            border-radius: 1.1rem;
            box-shadow: 0 8px 22px rgba(140,70,64,0.05);
            margin-bottom: 1.15rem;
            padding: 0.35rem 0.9rem 0.12rem;
        }

        .st-key-trip-card,
        .st-key-travelers-shell,
        .st-key-summary-card,
        .st-key-shortlist-card {
            border-radius: 1.55rem;
            box-shadow: 0 14px 36px rgba(140,70,64,0.08);
            padding: 1.45rem 1.55rem 1.2rem;
        }

        .st-key-trip-card {
            background: var(--paper);
            border: 1px solid #EADBD2;
        }

        .st-key-travelers-shell {
            background: var(--paper);
            border: 1px solid rgba(47,90,73,0.16);
            color: #173D2E;
        }

        .st-key-summary-card {
            background: linear-gradient(120deg, #FFFDF9 0%, #FBE6C3 100%);
            border: 1px solid rgba(168,108,23,0.16);
        }

        .st-key-shortlist-card {
            background: linear-gradient(130deg, #FFFFFF 0%, #E8F1F3 100%);
            border: 1px solid rgba(99,152,169,0.30);
            margin-top: 1.5rem;
        }

        .st-key-itinerary-shell {
            margin-top: 2.25rem;
        }

        div[class*="st-key-itinerary-day-"] {
            background: rgba(255,255,255,0.96);
            border: 1px solid #EADBD2;
            border-left: 5px solid var(--lagoon);
            border-radius: 1.25rem;
            box-shadow: 0 10px 28px rgba(58,46,43,0.07);
            margin: 0.8rem 0;
            padding: 0.55rem 0.95rem;
        }

        div[class*="st-key-traveler-card-"] {
            background: rgba(255,255,255,0.83);
            border: 1px solid rgba(47,90,73,0.16);
            border-top: 5px solid var(--mint-dark);
            border-radius: 1.2rem;
            padding: 1rem 1.05rem 0.85rem;
        }

        .st-key-traveler-card-2,
        .st-key-traveler-card-5 {
            border-top-color: var(--nectarine) !important;
        }

        .st-key-traveler-card-3,
        .st-key-traveler-card-6 {
            border-top-color: var(--lagoon) !important;
        }

        .st-key-traveler-card-4 {
            border-top-color: var(--peach) !important;
        }

        div[class*="st-key-result-card-"] {
            background: rgba(255,255,255,0.98);
            border: 1px solid #EADBD2;
            border-top: 5px solid var(--lagoon);
            border-radius: 1.35rem;
            box-shadow: 0 10px 26px rgba(58,46,43,0.06);
            padding: 1.15rem 1.25rem 0.85rem;
            margin-bottom: 0.85rem;
        }

        div[class*="st-key-result-card-1-"] {
            border-top-color: var(--nectarine);
            box-shadow: 0 14px 34px rgba(140,70,64,0.10);
        }

        div[class*="st-key-result-card-2-"] {
            border-top-color: var(--peach);
        }

        div[class*="st-key-result-card-3-"] {
            border-top-color: var(--mint);
        }

        div[class*="st-key-result-card-"]:hover {
            box-shadow: 0 18px 42px rgba(140,70,64,0.12);
            transform: translateY(-2px);
            transition: all 160ms ease;
        }

        .ts-section-label {
            color: var(--page-primary);
            font-size: 0.76rem;
            font-weight: 800;
            letter-spacing: 0.13em;
            text-transform: uppercase;
        }

        .ts-helper {
            color: var(--muted);
            line-height: 1.55;
            margin-bottom: 1rem;
        }

        .ts-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.3rem 0 0.8rem;
        }

        .ts-chip {
            background: rgba(150,199,179,0.22);
            border: 1px solid rgba(47,90,73,0.14);
            border-radius: 999px;
            color: var(--mint-dark);
            display: inline-block;
            font-size: 0.76rem;
            font-weight: 800;
            padding: 0.28rem 0.58rem;
        }

        .ts-score {
            align-items: baseline;
            color: var(--nectarine-dark);
            display: flex;
            gap: 0.15rem;
        }

        .ts-score strong {
            font-family: inherit;
            font-size: 2.3rem;
            font-weight: 850;
            letter-spacing: -0.05em;
            line-height: 1;
        }

        .ts-score span {
            color: var(--muted);
            font-size: 0.8rem;
            font-weight: 700;
        }

        .ts-tradeoff {
            background: rgba(249,185,92,0.18);
            border-left: 3px solid var(--peach);
            border-radius: 0.45rem;
            color: #66513A;
            font-size: 0.82rem;
            margin: 0.35rem 0;
            padding: 0.45rem 0.65rem;
        }

        .ts-must-do-line {
            background: rgba(215,137,127,0.13);
            border-left: 3px solid var(--nectarine);
            border-radius: 0.45rem;
            color: var(--nectarine-dark);
            font-size: 0.82rem;
            font-weight: 800;
            margin: 0.4rem 0 0.7rem;
            padding: 0.42rem 0.65rem;
        }

        .ts-rejected-line {
            background: rgba(111, 79, 166, 0.10);
            border-left: 3px solid #6F4FA6;
            border-radius: 0.45rem;
            color: #5B3E8A;
            font-size: 0.82rem;
            font-weight: 750;
            margin: 0.4rem 0 0.7rem;
            padding: 0.42rem 0.65rem;
        }

        .ts-shortlist-meta {
            color: var(--muted);
            font-size: 0.78rem;
            margin-top: -0.45rem;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 1.25rem;
        }

        div[data-testid="stForm"] {
            border: 0;
            padding: 0;
        }

        div[data-testid="stAlert"] {
            border-radius: 0.85rem;
        }

        @media (max-width: 700px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }

            .st-key-hero-copy {
                padding: 0.5rem 0 1rem;
            }

            .st-key-hero h1 {
                font-size: clamp(2.6rem, 13vw, 3.7rem);
            }

            .ts-hero-visual {
                height: 17rem;
            }

            .st-key-trip-card,
            .st-key-travelers-shell,
            .st-key-summary-card,
            .st-key-shortlist-card {
                padding: 1.1rem 1rem 0.9rem;
            }

            .st-key-workspace-page {
                border-radius: 1.35rem;
                padding: 1rem 0.85rem 1.5rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
