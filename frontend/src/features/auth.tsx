"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ArrowRight, LockKey, ShieldCheck } from "@phosphor-icons/react";
import { api } from "@/lib/api";
import { safeReturn } from "@/lib/format";
import { useAuth, useStore } from "@/components/providers";
import { Button, ErrorNotice, Field, Notice } from "@/components/ui";

export function AuthScreen({
  mode = "login",
  admin = false,
}: {
  mode?: "login" | "register" | "reset";
  admin?: boolean;
}) {
  const params = useSearchParams();
  const router = useRouter();
  const auth = useAuth();
  const store = useStore();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [done, setDone] = useState(false);
  const resetToken = params.get("token");
  const title =
    mode === "register"
      ? "创建你的账户"
      : mode === "reset"
        ? resetToken
          ? "设置新密码"
          : "找回密码"
        : admin
          ? "登录平台管理"
          : "欢迎回来";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(undefined);
    const email = String(data.get("email") ?? "").trim();
    const password = String(data.get("password") ?? "");
    const scope = admin ? "admin" : "customer";
    try {
      if (mode === "reset") {
        await api(`customer/auth/${resetToken ? "reset-confirm" : "reset-request"}`, {
          method: "POST",
          json: resetToken ? { token: resetToken, new_password: password } : { email },
        });
        setDone(true);
        return;
      }
      if (mode === "register")
        await api("customer/auth/register", { method: "POST", json: { email, password } });
      await api(`${scope}/auth/login`, { method: "POST", json: { email, password } });
      await auth.authenticated(scope);
      router.replace(safeReturn(params.get("next"), admin ? "/admin" : "/products"));
      router.refresh();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-layout">
      <div className="auth-story">
        <span className="eyebrow">{admin ? "平台管理" : "A-COMMERCE"}</span>
        <h2>
          {admin ? (
            <>
              每笔订单，
              <br />
              有据可查。
            </>
          ) : (
            <>
              为日常所需，
              <br />
              找到合适的一件。
            </>
          )}
        </h2>
        <p>
          {admin
            ? "商品、库存、履约与售后，在同一个工作空间处理。"
            : "保存喜欢的商品，核对购买规格，随时追踪订单进度。"}
        </p>
        <div className="auth-story-icon">
          {admin ? <ShieldCheck weight="thin" size={112} /> : <LockKey weight="thin" size={112} />}
        </div>
      </div>
      <section className="auth-form">
        <h1>{title}</h1>
        <p className="muted">
          {mode === "register"
            ? "使用邮箱注册，密码至少 12 位。"
            : mode === "reset"
              ? "重置后所有已有登录会话会失效。"
              : "使用邮箱和密码继续。"}
        </p>
        <ErrorNotice error={error} />
        {done ? (
          <>
            <Notice>
              {resetToken
                ? "密码已更新，请使用新密码登录。"
                : "若邮箱对应有效账号，将收到重置邮件。"}
            </Notice>
            <Link className="button primary" href="/login">
              返回登录
            </Link>
          </>
        ) : (
          <form onSubmit={submit} className="stack">
            {!(mode === "reset" && resetToken) && (
              <Field label="邮箱">
                <input
                  type="email"
                  name="email"
                  autoComplete="username"
                  required
                  maxLength={254}
                  placeholder="你的邮箱地址"
                />
              </Field>
            )}
            {(mode !== "reset" || resetToken) && (
              <Field
                label={mode === "reset" ? "新密码" : "密码"}
                hint="12 至 128 位，支持粘贴密码。"
              >
                <input
                  name="password"
                  type="password"
                  required
                  minLength={12}
                  maxLength={128}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                />
              </Field>
            )}
            {mode === "reset" && !store.data?.mail_enabled && (
              <Notice>当前环境尚未启用邮件，暂不能发送重置链接。</Notice>
            )}
            <Button
              type="submit"
              busy={busy}
              disabled={mode === "reset" && !resetToken && !store.data?.mail_enabled}
            >
              {busy
                ? "正在处理…"
                : mode === "register"
                  ? "创建账户并登录"
                  : mode === "reset"
                    ? resetToken
                      ? "更新密码"
                      : "发送重置链接"
                    : "登录"}
              <ArrowRight size={18} />
            </Button>
          </form>
        )}
        {!admin && mode === "login" && (
          <div className="auth-links">
            <Link
              href={`/register${params.get("next") ? `?next=${encodeURIComponent(params.get("next") ?? "")}` : ""}`}
            >
              创建账户
            </Link>
            <Link href="/reset-password">忘记密码</Link>
          </div>
        )}
        {mode === "register" && (
          <p className="muted">
            已有账户？<Link href="/login">前往登录</Link>
          </p>
        )}
        <p className="auth-legal">
          继续前可阅读<Link href="/policies/privacy">隐私说明</Link>与
          <Link href="/policies/terms">使用条款</Link>。
        </p>
      </section>
    </div>
  );
}
