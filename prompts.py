"""Prompt builder for the jewelry mix-and-match pipeline.

Lives in its own module so it can be imported by both the Streamlit app
and offline test harnesses without dragging in UI code.
"""

import re

# Cut names that are ALSO gem names — Seedream tends to render an
# "emerald-cut diamond" as an actual green emerald. Replace the trigger
# word in-place with an unambiguous shape synonym + explicit color anchor.
_CUT_REWRITES = [
    (r"\bemerald[\s-]cut\b",
     "rectangular step-cut (the shape jewelers call emerald-cut, "
     "ALWAYS a colorless WHITE diamond, NEVER a green emerald gemstone)"),
    (r"\bmarquise[\s-]cut\b",
     "pointed-oval cut (the shape jewelers call marquise-cut, "
     "ALWAYS a colorless WHITE diamond, NEVER a colored gem)"),
    (r"\basscher[\s-]cut\b",
     "square step-cut (the shape jewelers call asscher-cut, "
     "ALWAYS a colorless WHITE diamond)"),
]


def _disambiguate_cut(desc: str) -> str:
    """Rewrite cut names that are also gem names so the model anchors on shape, not color."""
    for pattern, replacement in _CUT_REWRITES:
        desc = re.sub(pattern, replacement, desc, flags=re.IGNORECASE)
    return desc


def build_combine_prompt(image_specs, additional_specs):
    """Master prompt tuned for fal-ai/bytedance/seedream/v4/edit.

    Seedream v4 responds best to tight imperative prompts with crisp
    per-image extraction lines and short rule blocks. Avoid long meta
    framing — Seedream handles structural fidelity natively when told
    plainly.
    """
    active_specs = [s for s in image_specs if s["file"] and s["description"]]

    ref_lines = []
    component_summary = []
    extracted_descs = [
        _disambiguate_cut(s["description"].strip().rstrip("."))
        for s in active_specs
    ]
    for i, spec in enumerate(active_specs):
        n = i + 1
        desc = extracted_descs[i]
        ignore_clause = (
            f"DO NOT use, reference, or borrow from any other part of Image {n} "
            f"— extract ONLY the {desc} from Image {n}. Ignore every other "
            f"component visible in Image {n} (the head, shank, halo, gallery, "
            "prongs, side stones, and any structural or decorative feature that "
            f"is NOT part of the {desc}). Those parts MUST NOT appear in the output."
        )
        ref_lines.append(
            f"- From Image {n}: incorporate the {desc} into the new ring "
            f"and fuse it seamlessly with the other component(s). Reproduce "
            f"this incorporated part 1:1 — exact metal color (rose-gold "
            f"stays rose-gold, yellow-gold stays yellow-gold, white-gold "
            f"stays white-gold, platinum stays platinum), shape, prongs, "
            f"stones, surface finish, profile, width, taper, and "
            f"proportions. {ignore_clause}"
        )
        component_summary.append(f"the {desc} (from Image {n})")

    refs_block = "\n".join(ref_lines)
    components_text = " + ".join(component_summary)

    component_checklist = "\n".join(
        f"- Is the {extracted_descs[i]} from Image {i+1} "
        f"visibly present in the output? It MUST be."
        for i, _ in enumerate(active_specs)
    )

    sections = [
        "OUTPUT EXACTLY ONE (1) FULLY-ASSEMBLED RING — a single complete "
        "wearable piece of jewelry. NOT two rings. NOT a wedding-band "
        "stack. NOT overlapping or interlocked rings. NOT a side-by-side "
        "collage. NOT one ring with a duplicate behind it. NOT floating "
        "or disconnected components. NOT an exploded-view diagram showing "
        "parts separately. NOT a 'before-assembly' layout. NOT a head "
        "alone. NOT a shank alone. The head and shank MUST be FUSED into "
        "ONE continuous cast piece of metal with no gap, no blank space, "
        "no air, between them. If any part of the output looks detached "
        "or hovering, it is a failure.",

        "Assemble the new ring by INCORPORATING the specified components "
        "from the reference images. This is a precise part-swap — each "
        "incorporated component is reproduced exactly, then FUSED into a "
        "single complete ring. Only the joint where components meet may "
        "be invented.",

        f"ASSEMBLE THE NEW RING USING THESE SOURCES\n{refs_block}",

        "CONSTRUCTION\n"
        f"Assemble: {components_text}. Connect them with a clean, "
        "structurally sound joint. Two-tone (or three-tone) metal is correct "
        "and intended — do not unify, average, or harmonize metal colors "
        "across components. If one component is rose-gold and the other is "
        "white-gold, the output shows rose-gold meeting white-gold at a "
        "crisp boundary. If three different metal colors appear across the "
        "components, all three appear in the output.",

        "EXCLUSION RULES (CRITICAL)\n"
        "- The output's shank comes ONLY from the image whose shank was "
        "extracted. Do NOT carry the shank — its pavé, stones, profile, or "
        "decoration — from any other reference image, even if it looks more "
        "typical or more 'jewelry-like'.\n"
        "- The output's head comes ONLY from the image whose head was "
        "extracted. Do NOT borrow prongs, halos, or settings from any other "
        "reference.\n"
        "- Specific failure to avoid: extracting a head from Image 1 and a "
        "shank from Image 2, then producing an output where the shank is a "
        "blend of BOTH shanks (e.g., plain band with pavé added from Image 1, "
        "or Image 2's plain band with Image 1's pavé grafted on). The output "
        "shank must be a faithful reproduction of Image 2's shank ALONE.\n"
        "- Do NOT borrow structural features (cutouts, gaps, openings, "
        "bezel housings, tension brackets, frame windows) from a component "
        "you are NOT extracting. If Image 2's bezel structure is not part "
        "of the extracted shank, it must NOT appear in the output.",

        "ANTI-COPY RULE (CRITICAL)\n"
        "- The output MUST NOT be a copy or near-copy of any single "
        "reference image. It is a NEW piece that visibly fuses parts from "
        "every reference.\n"
        "- The output MUST visibly contain the specified component from "
        "EVERY reference image listed above. Missing any one of them is a "
        "failure.\n"
        "- If your output looks like Image 1 with no Image 2 elements (or "
        "vice versa), discard it and try again — that is the most common "
        "failure mode for this task.\n"
        "- Specifically: a viewer comparing the output side-by-side with "
        "the references must immediately see which part came from which "
        "reference.",

        "SILHOUETTE PRESERVATION (CRITICAL)\n"
        "- Reproduce the exact 2D outline and silhouette of each extracted "
        "component. If the head is shaped like a flower with petals, the "
        "output head is shaped like a flower with petals. If the head is a "
        "6-prong solitaire, the output head is a 6-prong solitaire.\n"
        "- Decorative elements that define the shape — petals, halos, "
        "leaves, scrollwork, filigree, openwork, basket cages — MUST appear "
        "in the output exactly as in the source.\n"
        "- Do NOT simplify an ornate component into a plain one (e.g., "
        "turning a flower halo into a bare 4-prong setting).\n"
        "- Do NOT embellish a plain component into an ornate one.\n"
        "- Color alone is not enough — match the SHAPE.",

        "SURFACE FINISH PRESERVATION\n"
        "- DEFAULT to high-polished, mirror-finish metal unless the source "
        "clearly shows a matte / brushed / satin / hammered / sandblasted "
        "texture. When in doubt, render polished — never default to matte.\n"
        "- If the source surface is high-polished and smooth, the output "
        "surface is high-polished and smooth.\n"
        "- Do NOT add brushed, hammered, knurled, satin, matte, sandblasted, "
        "wood-grain, or any textured finish that is not in the source.\n"
        "- Do NOT add milgrain edges, engraving lines, or hatching that is "
        "not in the source.\n"
        "- Do NOT add 3D structural ornament along the shank — specifically "
        "rope, cable, twist, braid, herringbone, spiral, beaded, ridged, "
        "fluted, scalloped, or carved-channel patterns — UNLESS that exact "
        "ornament is clearly visible on the source shank being extracted. "
        "If the source shank is a flat or domed pavé band with no central "
        "ornament, the output shank is a flat or domed pavé band with NO "
        "central ornament. Inventing a rope/cable spine down the middle of "
        "a pavé shank is a hard failure.",

        "MANUFACTURABILITY (CRITICAL)\n"
        "- The output must be a PHYSICALLY REALIZABLE piece of jewelry that "
        "could be cast, set, and worn in real life.\n"
        "- The INTERIOR of the shank (the surface that touches the finger) "
        "MUST be smooth and ROUND/CIRCULAR — never hexagonal, octagonal, "
        "polygonal, faceted, or angular. Even when the OUTSIDE of the "
        "shank has flat or geometric faces (architectural shanks, square "
        "profiles, etc.), the INSIDE remains a smooth circle so the ring "
        "fits a finger.\n"
        "- Every prong, claw, gallery, halo, and decorative element must "
        "be structurally connected to the metal — no floating elements, "
        "no impossible cantilevers, no detached components.\n"
        "- Every stone must sit in a plausible setting (prong, bezel, "
        "channel, pavé, flush) with visible metal supporting it from below "
        "and around. No stones floating in air.\n"
        "- Realistic proportions and weight balance — the piece must look "
        "like jewelry a real craftsman could actually fabricate.",

        "INTEGRATION (CRITICAL — single cast piece, not a collage)\n"
        "- The head and shank must read as ONE integrated cast piece of "
        "jewelry, not two images pasted together. Metal must FLOW "
        "continuously from the shank up into the head's gallery, basket, "
        "and prong base. There must be no visible seam, gap, weld line, "
        "or step where the components meet.\n"
        "- Render a proper UNDERGALLERY/BASKET beneath the head — the "
        "structural metal cage that holds the prongs and bridges to the "
        "shoulders of the shank. Real cast jewelry always has this.\n"
        "- The shank's shoulders MUST taper smoothly up into the head's "
        "base. The transition is curved, organic, and load-bearing — not "
        "an abrupt right-angle butt-joint.\n"
        "- Two-tone construction is fine, but the boundary between metals "
        "is a clean ALLOY-TO-ALLOY weld at a structural seam (e.g., "
        "around the basket), NOT a 2D color overlay.\n"
        "- A jeweler looking at the output should see how it would be "
        "cast in one piece (or assembled with proper joinery), not how "
        "two photos were photoshopped together.",

        "PERSPECTIVE COHERENCE\n"
        "- The head and shank MUST be rendered from the same camera angle "
        "with the same perspective, focal length, and lighting direction. "
        "If the shank is shown in a 3/4 view tilted to the right, the "
        "head sits on it in the SAME 3/4 perspective — not facing the "
        "camera head-on while the shank tilts away.\n"
        "- Cast shadows, highlights, and reflections on the head and "
        "shank must come from the SAME light source.\n"
        "- The head's tilt and rotation must be consistent with the band's "
        "orientation. The center stone faces the same direction the "
        "shoulders rise toward.",

        "SYMMETRY (CRITICAL)\n"
        "- Decorative elements (pavé stones, channel-set stones, side "
        "diamonds, milgrain, halos, prongs, scrollwork) MUST appear "
        "symmetrically on BOTH shoulders and BOTH sides of the piece. If "
        "the source shank has pavé on both shoulders, the output shank has "
        "pavé on both shoulders — never on one side only.\n"
        "- The 3/4 view must show BOTH shoulders of the ring, and both "
        "shoulders must be visibly mirror-images of each other (same "
        "decoration, same width, same taper). Do NOT decorate only the "
        "near-side shoulder while leaving the far-side shoulder plain.\n"
        "- The left and right halves of the ring mirror each other "
        "unless the source is intentionally asymmetric.\n"
        "- The head's prongs are evenly spaced and equal in size. If the "
        "source head has 4 prongs, the output has 4 prongs in the same "
        "X or + arrangement; if 6 prongs, 6 prongs evenly spaced.",

        "PRESERVATION RULES (STRICT)\n"
        "- Do NOT add stones, diamonds, pavé, channel-set stones, halos, "
        "milgrain, engraving, filigree, or any decoration that is not "
        "visible on that component in its source image.\n"
        "- Do NOT remove stones, prongs, or details that ARE visible in "
        "the source component.\n"
        "- If a component is plain and smooth in the source, keep it plain "
        "and smooth. If it is set with stones, keep exactly those stones in "
        "the same arrangement.\n"
        "- Do NOT change band thickness, shoulder profile, prong count, "
        "prong shape, or stone size.\n"
        "- Do NOT invent a rope / cable / twist / braid / herringbone / "
        "spiral / fluted spine running along the shank. If the source shank "
        "is pure pavé with no such ornament, the output shank is pure pavé "
        "with no such ornament — even if it 'looks more ornate' or 'feels "
        "more like jewelry' to add one.\n"
        "- Do NOT redesign, stylize, or reinterpret — copy.",

        "STONE COLOR / CUT DISAMBIGUATION (CRITICAL)\n"
        "- All center stones, side stones, accent stones, and pavé stones "
        "in the output are COLORLESS WHITE DIAMONDS (D–F color, ice-clear "
        "brilliance, white sparkle), UNLESS the source image clearly shows "
        "a colored gemstone (visibly green / blue / red / pink / etc.) "
        "or the user's description explicitly specifies a colored gem.\n"
        "- Cut names refer ONLY to the geometric SHAPE of the stone, NEVER "
        "to its material or color. Treat them as shape labels only:\n"
        "  • emerald-cut  = rectangular step-cut DIAMOND  (NOT a green emerald)\n"
        "  • marquise-cut = pointed oval DIAMOND          (NOT a colored gem)\n"
        "  • princess-cut = square brilliant DIAMOND\n"
        "  • asscher-cut  = square step-cut DIAMOND\n"
        "  • pear / oval / cushion / radiant / heart / baguette / "
        "trillion / round = corresponding-shape DIAMONDS\n"
        "- Common failure to avoid: rendering an 'emerald-cut diamond' as "
        "an actual green emerald gemstone. Do NOT do this. If the source "
        "stone is colorless and faceted like ice, the output stone is "
        "colorless and faceted like ice — only the shape carries over.",

        "COVERAGE / EXTENT (CRITICAL)\n"
        "- Preserve the EXTENT and COVERAGE area of every decorative "
        "feature, not just its presence. If the source shank has pavé "
        "running the FULL LENGTH of the band (from shoulder to shoulder), "
        "the output shank has pavé running the FULL LENGTH. Do NOT shrink "
        "full-length pavé into shoulder-only pavé.\n"
        "- If a halo wraps fully around the center stone in the source, it "
        "wraps fully in the output. Do NOT show a partial halo.\n"
        "- If milgrain, engraving, or filigree spans an entire edge in the "
        "source, it spans the entire edge in the output. Do NOT shorten "
        "or interrupt continuous decoration.\n"
        "- Match the START point and END point of every decorative band, "
        "not just the style.",
    ]

    if additional_specs.get("metal"):
        sections.append(
            f"METAL OVERRIDE\nRender the entire piece in {additional_specs['metal']}, "
            "replacing the original metal colors of all references. This "
            "overrides the color preservation rules above."
        )
        # Capture the override so the validator's target summary can reflect
        # the final intended metal rather than the per-reference colors.
        additional_specs["_metal_applied"] = additional_specs["metal"]

    if additional_specs.get("stones"):
        sections.append(f"STONES\n{additional_specs['stones']}")

    if additional_specs.get("dimensions"):
        sections.append(f"PROPORTIONS\n{additional_specs['dimensions']}")

    if additional_specs.get("notes"):
        sections.append(f"NOTES\n{additional_specs['notes']}")

    sections.append(
        "OUTPUT\n"
        "One ring on a PURE WHITE seamless background — RGB(255,255,255), "
        "not gray, not cream, not off-white, not ivory, not warm-tinted, "
        "not tinted — with only a subtle soft shadow directly under the "
        "piece. IGNORE the background color of any input reference image; "
        "if a reference has a cream or tinted background, the output "
        "background is STILL pure white RGB(255,255,255). Professional "
        "jewelry product photography, neutral-balanced studio lighting "
        "(no warm cast), ultra-sharp macro detail, centered three-quarter "
        "angle, the piece occupying roughly 70% of the frame. No hands, "
        "no models, no props, no text, no watermarks, no logos, no "
        "collage of references."
    )

    sections.append(
        f"VERIFY BEFORE FINALIZING\n{component_checklist}\n"
        f"- Does each extracted component keep its original metal color? It MUST.\n"
        f"- Are all stones colorless white diamonds (unless the source clearly "
        f"shows a colored gemstone)? They MUST be — an 'emerald-cut' is a "
        f"rectangular DIAMOND, not a green emerald.\n"
        f"- Does each extracted component keep its original SHAPE/silhouette "
        f"(petals, halos, prong count, band profile)? It MUST.\n"
        f"- Are all surfaces in the output finished the same way as their "
        f"source (polished defaults to polished, no added brushed/matte)? They MUST be.\n"
        f"- Has any rope/cable/twist/braid/herringbone/spiral spine been "
        f"invented on the shank that is NOT visible on the source shank? "
        f"It MUST NOT — flag this as a hard defect if present.\n"
        f"- Are decorative elements (pavé, prongs, side stones) symmetric "
        f"across both shoulders / both sides? They MUST be.\n"
        f"- Does decoration cover the SAME EXTENT as in the source (full-length "
        f"pavé stays full-length, not shrunk to shoulders only)? It MUST.\n"
        f"- Is the background pure white RGB(255,255,255) — not gray, cream, "
        f"or warm-tinted? It MUST be.\n"
        f"- Has any non-extracted component (e.g., the shank of Image 1 when "
        f"only its head was extracted) leaked into the output? It MUST NOT.\n"
        f"- Is the interior of the shank smooth and round (manufacturable, "
        f"wearable on a real finger)? It MUST be.\n"
        f"- Do the head and shank read as ONE integrated cast piece "
        f"(metal flows through a basket/gallery, no visible 2D paste-on "
        f"seam)? They MUST.\n"
        f"- Are the head and shank rendered from the SAME camera angle "
        f"and lit by the SAME light source? They MUST be.\n"
        f"- Are BOTH shoulders visible in the 3/4 view, and do they "
        f"mirror each other in decoration and shape? They MUST.\n"
        f"- Is the output clearly a NEW piece (not a copy of any single reference)? "
        f"It MUST be.\n"
        f"- Does the output show EXACTLY ONE ring object (not two rings "
        f"overlapping, not a wedding-band stack, not a side-by-side pair, "
        f"not floating disconnected components, not an exploded-view of "
        f"head + shank as separate objects)? It MUST.\n"
        f"- Are the head and shank FUSED into one continuous metal piece "
        f"with no gap or air between them? They MUST be."
    )

    return "\n\n".join(sections)


def build_target_summary(image_specs, additional_specs):
    """One plain-language paragraph describing the intended output.

    Used by the AI validator as the ground truth to compare the generated
    image against. Stripped of all rule blocks, anti-failure rants, and
    formatting directives — those are for the image model, not the
    reviewer.
    """
    active_specs = [s for s in image_specs if s.get("file") and s.get("description")]
    parts = [
        _disambiguate_cut(s["description"].strip().rstrip("."))
        for s in active_specs
    ]
    components_text = " + ".join(f"the {p}" for p in parts) if parts else "the assembled ring"

    sentences = [
        f"A single fully-assembled ring that fuses {components_text} "
        "into one continuous cast piece, with the head and shank "
        "structurally connected via a proper undergallery / basket."
    ]

    if additional_specs.get("_metal_applied") or additional_specs.get("metal"):
        metal = additional_specs.get("_metal_applied") or additional_specs["metal"]
        sentences.append(f"The entire piece is rendered in {metal}.")
    else:
        sentences.append(
            "Each extracted component keeps its original metal color "
            "(rose-gold stays rose-gold, white-gold stays white-gold, "
            "yellow-gold stays yellow-gold, platinum stays platinum)."
        )

    if additional_specs.get("stones"):
        sentences.append(f"Stones: {additional_specs['stones'].strip()}.")
    else:
        sentences.append(
            "All faceted stones are colorless white diamonds unless a "
            "colored gem is explicitly named in a component description."
        )

    if additional_specs.get("dimensions"):
        sentences.append(f"Proportions: {additional_specs['dimensions'].strip()}.")
    if additional_specs.get("notes"):
        sentences.append(f"Notes: {additional_specs['notes'].strip()}.")

    sentences.append(
        "Background is pure white RGB(255,255,255). Three-quarter angle, "
        "polished surfaces unless the source clearly shows a matte finish, "
        "decoration symmetric across both shoulders."
    )

    return " ".join(sentences)


def build_correction_addendum(diagnosis: dict, attempt_number: int) -> str:
    """Turn a validator diagnosis into a corrective directive for the image model.

    Appended to the master prompt on retry passes. The key idea is to tell
    the model both what to PRESERVE (so we don't regress on what was
    already right) and what to FIX, with the validator's own suggestion
    as the headline directive.
    """
    suggestion = (diagnosis.get("suggestion") or "").strip()
    correct = [c for c in (diagnosis.get("correct") or []) if c]
    missing = [m for m in (diagnosis.get("missing") or []) if m]
    wrong = [w for w in (diagnosis.get("wrong") or []) if w]
    score = diagnosis.get("score", 0)

    lines = [
        f"CORRECTION PASS (attempt {attempt_number}) — TARGETED FIX, NOT A FRESH RENDER",
        f"The previous attempt scored {score}/100. Treat that attempt "
        "as the working baseline: keep everything that was correct, "
        "fix only the specific defects listed below, and do not "
        "regenerate from scratch.",
    ]
    if correct:
        lines.append(
            "PRESERVE these — they were rendered correctly and MUST NOT "
            "change in this attempt: "
            + "; ".join(correct)
            + "."
        )
    if missing:
        lines.append(
            "ADD these components — they are absent from the previous "
            "attempt and MUST appear in this attempt: "
            + "; ".join(missing)
            + "."
        )
    if wrong:
        lines.append(
            "FIX these — present but rendered incorrectly, must be "
            "corrected to match the target: "
            + "; ".join(wrong)
            + "."
        )
    if suggestion:
        lines.append(f"PRIMARY DIRECTIVE: {suggestion}")
    lines.append(
        "Critical: do NOT introduce new defects in the parts that were "
        "already correct. This is a surgical fix on a working baseline, "
        "not a fresh design pass."
    )
    return "\n".join(lines)
