import { ApiError } from "../api/client";

export function failure(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return `版本或状态冲突（${error.code}）。请刷新后重新审核；本次操作未确认成功。`;
  return error instanceof Error ? error.message : "请求失败，请重试。";
}

export function Evidence({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}
