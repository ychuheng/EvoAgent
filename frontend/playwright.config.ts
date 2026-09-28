import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: {
    // 用 npx 解析本地依赖而不是硬编码 pnpm：CI/Docker 里有 pnpm，但只装了 Node 的机器上
    // `pnpm` 常常不在 PATH，会让整套浏览器流程无法启动（复核时实际踩到）。
    command: "npx --no-install vite --host 127.0.0.1 --port 4173",
    port: 4173,
    reuseExistingServer: !process.env.CI,
  },
});
