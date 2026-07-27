import assert from "node:assert/strict";
import test from "node:test";

import {
  currentConfigurationTab,
  rememberConfigurationTab,
} from "../../../frontend/console/configuration.js";

test("配置页签只在当前页面模块生命周期内记忆", () => {
  assert.equal(currentConfigurationTab(), "agents");
  assert.equal(rememberConfigurationTab("mcp"), "mcp");
  assert.equal(currentConfigurationTab(), "mcp");
  assert.equal(rememberConfigurationTab("unknown"), "mcp");
});
