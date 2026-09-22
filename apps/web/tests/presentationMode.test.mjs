import assert from "node:assert/strict";
import test from "node:test";
import { resolvePresentationMode } from "../src/shared/presentationMode.ts";

test("restores original-deck mode while content is still being built", () => {
  assert.equal(resolvePresentationMode({
    contentJob: { organization_mode: "source_deck" },
  }), "source_deck");
});

test("opening original content without a plan overrides a stale generated selection", () => {
  assert.equal(resolvePresentationMode({
    content: { organization_mode: "source_deck" },
    presentationMode: "generated",
  }), "source_deck");
});

test("knowledge content does not become original-deck mode on reload", () => {
  assert.equal(resolvePresentationMode({
    content: { organization_mode: "knowledge" },
  }), "generated");
});

test("preserves the selected route before building content", () => {
  for (const presentationMode of ["generated", "source_deck", "paper_deck"]) {
    assert.equal(resolvePresentationMode({ presentationMode }), presentationMode);
  }
});

test("an existing presentation plan determines the route", () => {
  assert.equal(resolvePresentationMode({
    presentationPlan: { mode: "paper_deck" },
    content: { organization_mode: "source_deck" },
  }), "paper_deck");
});

test("preserves paper workflow selection for knowledge content", () => {
  assert.equal(resolvePresentationMode({
    content: { organization_mode: "knowledge" },
    presentationMode: "paper_deck",
  }), "paper_deck");
});
