import type { ReactNode } from "react";

interface AuthCardProps {
  title: string;
  message: string;
  children: ReactNode;
  footer?: ReactNode;
}

/**
 * 认证卡片:居中单栏,最大 22rem,radius-card + surface + border + shadow-1。
 * 标题带脉冲双点装饰(1s 循环,prefers-reduced-motion 下静止)。
 */
export default function AuthCard({ title, message, children, footer }: AuthCardProps) {
  return (
    <main id="main" className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="card w-full max-w-[22rem] p-5 shadow-1">
        <h1 className="auth-dots mb-1 text-xl font-semibold tracking-tight text-accent">
          ✦ {title}
        </h1>
        <p className="mb-4 text-sm text-text-dim">{message}</p>
        {children}
        {footer ? <div className="mt-4 text-center">{footer}</div> : null}
      </div>
    </main>
  );
}