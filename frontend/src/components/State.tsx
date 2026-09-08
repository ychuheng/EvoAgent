import type { ReactNode } from "react";

export function Loading() {
  return <p className="state">正在读取服务端事实…</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state empty">{children}</p>;
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="error" role="alert">
      {message}
    </div>
  );
}

export function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${value}`}>{value.replaceAll("_", " ")}</span>;
}
