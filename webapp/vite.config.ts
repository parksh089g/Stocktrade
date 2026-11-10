// 웹앱 빌드 설정. 개발: npm run dev, 데모 데이터로 화면 보기: npm run demo, 시험: npm test
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 종목 이름은 엔진과 같은 목록(config/universe.toml)에서 읽어 빌드에 넣는다.
// 형식이 단순해서([[KR]] 아래 symbol = "...", name = "...") TOML 라이브러리 없이 읽는다.
function symbolNames(): Record<string, string> {
  const path = fileURLToPath(new URL("../config/universe.toml", import.meta.url));
  const names: Record<string, string> = {};
  let block: { symbol?: string; name?: string } = {};
  const flush = () => {
    if (block.symbol && block.name) names[block.symbol] = block.name;
    block = {};
  };
  for (const raw of readFileSync(path, "utf8").split("\n")) {
    const line = raw.trim();
    if (line.startsWith("[[")) {
      flush();
      continue;
    }
    const m = /^(symbol|name)\s*=\s*"([^"]*)"/.exec(line);
    if (m) block[m[1] as "symbol" | "name"] = m[2];
  }
  flush();
  return names;
}

function appVersion(): string {
  try {
    return execSync("git rev-parse --short HEAD", { encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
}

export default defineConfig({
  define: {
    __SYMBOL_NAMES__: JSON.stringify(symbolNames()),
    __APP_VERSION__: JSON.stringify(appVersion()),
    __BUILT_AT__: JSON.stringify(new Date().toISOString()),
  },
  esbuild: { jsx: "automatic", jsxImportSource: "preact" },
  build: { target: "es2022" },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
