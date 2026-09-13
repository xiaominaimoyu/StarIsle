import type { ReactNode } from "react";

interface AuthCardProps {
  title: string;
  message: string;
  children: ReactNode;
  footer?: ReactNode;
}

/**
 * 认证入口:保留表单逻辑，使用可读的来信/档案版式承接公开页。
 */
export default function AuthCard({ title, message, children, footer }: AuthCardProps) {
  return (
    <div className="auth-letter">
      <p className="auth-letter__eyebrow">PRIVATE CONSOLE · ACCESS</p>
      <h1>{title}</h1>
      <p className="auth-letter__message">{message}</p>
      {children}
      {footer ? <div className="mt-5 border-t border-border pt-4 text-center">{footer}</div> : null}
    </div>
  );
}
