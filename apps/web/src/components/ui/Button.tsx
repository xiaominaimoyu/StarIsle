import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  children: ReactNode;
}

const variantClass: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-foreground hover:brightness-110",
  secondary: "border border-border bg-transparent hover:border-accent/60",
  ghost: "bg-transparent hover:bg-surface",
  danger: "bg-danger text-ink data-[light=true]:text-white hover:brightness-110",
};

const sizeClass: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-base",
  lg: "h-12 px-6 text-base",
};

export default function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  className = "",
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      data-light={variant === "danger" && typeof document !== "undefined" ? document.documentElement.dataset.theme === "light" : undefined}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors duration-150 focus-ring disabled:cursor-not-allowed disabled:opacity-60 ${variantClass[variant]} ${sizeClass[size]} ${className}`}
      {...rest}
    >
      {loading ? <span aria-hidden="true" className="inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent" /> : null}
      <span className={loading ? "sr-only" : undefined}>{children}</span>
      {loading ? <span aria-live="polite" className="sr-only">加载中</span> : null}
    </button>
  );
}
