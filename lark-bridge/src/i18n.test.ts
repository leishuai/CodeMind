import { describe, it, expect } from "vitest";
import { detectLang, messages } from "./i18n.js";

describe("detectLang", () => {
  it("defaults to English for a non-Chinese timezone", () => {
    expect(detectLang({}, "UTC")).toBe("en");
  });

  it("uses Chinese for mainland-China timezones", () => {
    expect(detectLang({}, "Asia/Shanghai")).toBe("zh");
    expect(detectLang({}, "Asia/Urumqi")).toBe("zh");
  });

  it("uses English for non-Chinese timezones", () => {
    expect(detectLang({}, "America/New_York")).toBe("en");
    expect(detectLang({}, "Europe/London")).toBe("en");
  });

  it("lets AUTOMIND_LANG override the timezone", () => {
    expect(detectLang({ AUTOMIND_LANG: "en" }, "Asia/Shanghai")).toBe("en");
    expect(detectLang({ AUTOMIND_LANG: "zh" }, "America/New_York")).toBe("zh");
  });

  it("treats any zh* value as Chinese and everything else as English", () => {
    expect(detectLang({ AUTOMIND_LANG: "zh-CN" }, undefined)).toBe("zh");
    expect(detectLang({ AUTOMIND_LANG: "en-US" }, "Asia/Shanghai")).toBe("en");
    expect(detectLang({ AUTOMIND_LANG: "fr" }, "Asia/Shanghai")).toBe("en");
  });
});

describe("messages", () => {
  it("names Feishu explicitly in the scan prompt for both languages", () => {
    expect(messages("en").scanPrompt).toContain("Feishu app");
    expect(messages("zh").scanPrompt).toContain("飞书 App");
  });

  it("clarifies the workspace is not auto-detected from the launch dir", () => {
    expect(messages("en").askWorkspaceIntro).toContain("NOT auto-detected");
    expect(messages("zh").askWorkspaceIntro).toContain("不会根据");
  });

  it("returns the English catalog by default", () => {
    expect(messages("en").usage).toContain("codemind channel");
    expect(messages("zh").usage).toContain("codemind channel");
  });

  it("offers both create-new and bind-existing credential choices", () => {
    for (const lang of ["en", "zh"] as const) {
      const m = messages(lang);
      expect(m.credChoiceCreate).toBeTruthy();
      expect(m.credChoiceBind).toBeTruthy();
      expect(m.bindAppIdPrompt).toContain("AppID");
      expect(m.bindAppSecretPrompt).toContain("AppSecret");
      expect(m.bindSaved("cli_x")).toContain("cli_x");
    }
  });

  it("offers a keep/create/bind menu when credentials already exist", () => {
    for (const lang of ["en", "zh"] as const) {
      const m = messages(lang);
      expect(m.credManageHeader).toBeTruthy();
      expect(m.credManageUseCurrent).toBeTruthy();
      expect(m.credManageCreate).toBeTruthy();
      expect(m.credManageBind).toBeTruthy();
      expect(m.credManagePrompt).toContain("1-3");
    }
  });

  it("warns about the same-app connection-limit guard in both languages", () => {
    for (const lang of ["en", "zh"] as const) {
      const msg = messages(lang).appAlreadyConnected("bot_other", 4242, "cli_app");
      // Must name the conflicting bot, its pid, the shared appId, and the stop hint.
      expect(msg).toContain("bot_other");
      expect(msg).toContain("4242");
      expect(msg).toContain("cli_app");
      expect(msg).toContain("codemind channel stop bot_other");
    }
  });
});
