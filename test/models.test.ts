import { expect, test } from "bun:test";
import { isLocalModel, isLoopback } from "../src/song/models";

test("local models are recognised by endpoint or runtime, not by name", () => {
  for (const u of ["http://127.0.0.1:8080/v1", "http://localhost:11434", "http://[::1]:1234", "http://studio.local:8000"]) expect(isLoopback(u)).toBe(true);
  for (const u of ["https://api.actual.inc/v1", "https://api.anthropic.com", "http://10.0.0.5:8080", "", undefined]) expect(isLoopback(u)).toBe(false);
  expect(isLocalModel("ollama", undefined)).toBe(true);
  expect(isLocalModel("actual", "http://127.0.0.1:8080/v1")).toBe(true);
  expect(isLocalModel("actual", "https://api.actual.inc/v1")).toBe(false);
  expect(isLocalModel("anthropic", "https://api.anthropic.com")).toBe(false);
});
