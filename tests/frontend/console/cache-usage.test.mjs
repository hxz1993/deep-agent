import test from "node:test";
import assert from "node:assert/strict";
import { cacheSummary, cacheMessageSummary } from "../../../console/cache-usage.js";
import { savedRunFacts } from "../../../console/session-presentation.js";

const response = (input, read, write, reported = true) => ({
  kind: "response", usage: { input_tokens: input, output_tokens: 12, cache_read_tokens: read, cache_write_tokens: write },
  metadata: { deepagent_cache_usage: { version: 1, read_reported: reported, write_reported: reported } },
});

test("cache ratio uses input-weighted reported totals, zero is different from unavailable", () => {
  const result = cacheSummary([response(10000, 8000, 0), response(6000, 3000, 0)]);
  assert.equal(result.hit_ratio, 0.6875);
  assert.equal(result.cache_write_tokens, 0);
  assert.equal(cacheMessageSummary(response(1000, 0, 0)).hit_ratio, 0);
  assert.equal(cacheMessageSummary(response(1000, 0, 0, false)).hit_ratio, null);
  assert.equal(cacheMessageSummary({kind:"response",usage:{input_tokens:1000, cache_read_tokens:0}}).hit_ratio, null);
});

test("partial counts never masquerade as a complete cache ratio", () => {
  const result = cacheSummary([response(10000, 8000, 0), {kind:"response",usage: {input_tokens:6000, output_tokens:1}}]);
  assert.equal(result.cache_read_tokens, 8000);
  assert.equal(result.read_complete, false);
  assert.equal(result.hit_ratio, null);
  assert.equal(cacheMessageSummary(response(100, 200, 0)).hit_ratio, null);
  for (const invalid of [-1, true, "20", 0.5]) {
    assert.equal(cacheMessageSummary(response(1000, invalid, 0)).cache_read_tokens, null);
  }
});

test("only explicit known provider aliases rescue old records; extensions stay untouched", () => {
  const message = {kind:"response",provider_name:"deepseek", usage:{input_tokens:1000,cache_read_tokens:0,details:{prompt_cache_hit_tokens:0, mysterious_cache:900}}};
  const before = JSON.stringify(message);
  assert.equal(cacheMessageSummary(message).hit_ratio, 0);
  assert.equal(cacheMessageSummary({...message,provider_name:"unknown"}).hit_ratio, null);
  assert.equal(JSON.stringify(message), before);
});

test("split and replayed parts contribute a model response once", () => {
  const events = [0,1].map(part => ({event_id:`e${part}`,event_type:"assistant/message",run_id:"r",sequence:part+1,
    payload:{source:{message_ordinal:1,part_index:part,part_key:`1:${part}`},
      message:{...response(10000,8000,0),parts:[{part_kind:"text",content:"sample"}]}}}));
  const facts = savedRunFacts([...events, events[0]]);
  assert.equal(facts.cache_usage.response_count,1);
  assert.equal(facts.cache_usage.cache_read_tokens,8000);
  assert.equal(facts.cache_usage.hit_ratio,0.8);
});
