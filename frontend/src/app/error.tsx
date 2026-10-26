"use client";

import { Button, PageTitle } from "@/components/ui";
export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <section className="empty">
      <PageTitle
        title="页面暂时未能加载"
        description="你的订单结果以服务端为准。恢复连接后可重试，或在订单页核对已有交易。"
      />
      <Button onClick={reset}>重新加载</Button>
    </section>
  );
}
