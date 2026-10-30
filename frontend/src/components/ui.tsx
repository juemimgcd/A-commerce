"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { createPortal } from "react-dom";
import { WarningCircle, ArrowRight, X, Package, ArrowClockwise } from "@phosphor-icons/react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type ButtonHTMLAttributes,
} from "react";
import { api, ApiError } from "@/lib/api";
import { money, statusLabel } from "@/lib/format";
import type { Scope } from "@/lib/types";
import { useAuth } from "./providers";

export function Button({
  children,
  variant = "primary",
  busy,
  className = "",
  type = "button",
  onClick,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      type={type}
      onClick={(event) => {
        event.currentTarget.focus({ preventScroll: true });
        onClick?.(event);
      }}
      className={`button ${variant} ${className}`}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
    >
      {children}
    </button>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {action}
    </header>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}
export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const pathname = usePathname();
  const search = new URLSearchParams(useSearchParams().toString());
  search.delete("token");
  const next = `${pathname}${search.size ? `?${search}` : ""}`;
  const [reauth, setReauth] = useState(false);
  const [verifiedError, setVerifiedError] = useState<unknown>();
  if (!error) return null;
  const message = error instanceof Error ? error.message : "操作未完成，请重试。";
  const login = error instanceof ApiError && error.status === 401;
  const recent = error instanceof ApiError && error.code === "RECENT_AUTH_REQUIRED";
  const verified = recent && verifiedError === error;
  return (
    <>
      <div className={`notice ${verified ? "" : "error"}`} role={verified ? "status" : "alert"}>
        <WarningCircle size={22} aria-hidden />
        <div>
          <strong>{verified ? "身份已验证，请核对最新状态后再次提交原操作。" : message}</strong>
          {error instanceof ApiError && error.retryAfter > 0 && (
            <p>请在约 {Math.ceil(error.retryAfter)} 秒后重试。</p>
          )}
          {error instanceof ApiError && error.requestId && (
            <p className="request-id">请求编号：{error.requestId}</p>
          )}
          {recent ? (
            <Button variant="ghost" onClick={() => setReauth(true)}>
              {verified ? "再次验证身份" : "重新验证身份，保留当前内容"}
            </Button>
          ) : login ? (
            <Link
              href={`${pathname.startsWith("/admin") ? "/admin/login" : "/login"}?next=${encodeURIComponent(next)}`}
            >
              重新登录 <ArrowRight />
            </Link>
          ) : (
            retry && (
              <Button variant="ghost" onClick={retry}>
                <ArrowClockwise />
                重试
              </Button>
            )
          )}
        </div>
      </div>
      <Modal open={reauth} title="重新验证身份" onClose={() => setReauth(false)}>
        {reauth && (
          <Reauthenticate
            scope={pathname.startsWith("/admin") ? "admin" : "customer"}
            onDone={() => {
              setVerifiedError(error);
              setReauth(false);
            }}
          />
        )}
      </Modal>
    </>
  );
}
function Reauthenticate({ scope, onDone }: { scope: Scope; onDone: () => void }) {
  const auth = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const data = new FormData(event.currentTarget);
        setBusy(true);
        setError("");
        try {
          await api(`${scope}/auth/login`, {
            method: "POST",
            json: { email: auth[scope]?.email, password: String(data.get("password") ?? "") },
          });
          await auth.authenticated(scope, true);
          setDone(true);
        } catch (value) {
          setError(value instanceof Error ? value.message : "身份验证未完成。");
        } finally {
          setBusy(false);
        }
      }}
    >
      <p>{auth[scope]?.email}</p>
      {done ? (
        <>
          <Notice>身份已更新。请关闭此窗口，核对最新业务状态后，再次确认原操作。</Notice>
          <Button type="button" onClick={onDone}>
            返回原操作
          </Button>
        </>
      ) : (
        <>
          <Field label="当前密码">
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              minLength={12}
              maxLength={128}
              autoFocus
            />
          </Field>
          {error && (
            <p role="alert" className="error-text">
              {error}
            </p>
          )}
          <Button type="submit" busy={busy}>
            验证身份
          </Button>
        </>
      )}
    </form>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice" role="status">
      {children}
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Package size={40} weight="light" aria-hidden />
      <h2>{title}</h2>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}
export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-group" aria-busy="true" aria-label="正在加载">
      <span className="sr-only">正在加载内容</span>
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton" key={i} />
      ))}
    </div>
  );
}
export function Status({ value, label }: { value: string; label?: string }) {
  const tone = ["paid", "delivered", "succeeded", "done", "resolved"].includes(value)
    ? "success"
    : ["failed", "rejected", "exception", "manual_review"].includes(value)
      ? "danger"
      : ["unknown", "closing", "pending", "preparing"].includes(value)
        ? "warning"
        : "neutral";
  return (
    <span className={`status ${tone}`}>
      <span aria-hidden />
      {label ?? statusLabel(value)}
    </span>
  );
}
export function Money({ value, className = "" }: { value: number; className?: string }) {
  return <span className={`money ${className}`}>{money(value)}</span>;
}
const subscribeHydration = () => () => {};
export function Modal({
  open,
  title,
  children,
  onClose,
  wide = false,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const mounted = useSyncExternalStore(
    subscribeHydration,
    () => true,
    () => false,
  );
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, [open, mounted]);
  if (!mounted) return null;
  return createPortal(
    <dialog
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-body">
        <div className="modal-heading">
          <h2 id={titleId}>{title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="关闭">
            <X size={22} />
          </Button>
        </div>
        {children}
      </div>
    </dialog>,
    document.body,
  );
}
export function AuthGate({
  children,
  scope = "customer",
  permission,
}: {
  children: ReactNode;
  scope?: Scope;
  permission?: string;
}) {
  const auth = useAuth();
  const pathname = usePathname();
  const search = new URLSearchParams(useSearchParams().toString());
  search.delete("token");
  const next = `${pathname}${search.size ? `?${search}` : ""}`;
  if (scope === "admin" ? auth.adminLoading : auth.customerLoading) return <Loading />;
  const profile = auth[scope];
  if (!profile)
    return (
      <Empty
        title={scope === "admin" ? "登录平台管理" : "登录后继续"}
        action={
          <Link
            className="button primary"
            href={`${scope === "admin" ? "/admin/login" : "/login"}?next=${encodeURIComponent(next)}`}
          >
            登录并继续 <ArrowRight />
          </Link>
        }
      >
        登录后查看属于你的订单、地址和操作记录。
      </Empty>
    );
  if (permission && !profile.permissions.includes(permission))
    return (
      <Empty
        title="当前账号没有此项权限"
        action={
          <Link className="button secondary" href="/admin">
            返回工作台
          </Link>
        }
      >
        请联系拥有员工管理权限的管理员。
      </Empty>
    );
  return <>{children}</>;
}
export function DataTable({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label="数据表格">
      <table>
        <thead>
          <tr>
            {headers.map((title) => (
              <th key={title} scope="col">
                {title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
