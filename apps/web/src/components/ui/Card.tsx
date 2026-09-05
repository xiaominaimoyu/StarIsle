import type { AnchorHTMLAttributes, HTMLAttributes, ReactNode } from "react";

interface CardBaseProps {
  children: ReactNode;
  hover?: boolean;
  /** 卡片整体可点:用单个 <a> 包裹,不用堆叠链接 */
  as?: "div" | "a";
  href?: string;
}

type CardProps = CardBaseProps &
  (
    | (HTMLAttributes<HTMLDivElement> & { as?: "div" })
    | (AnchorHTMLAttributes<HTMLAnchorElement> & { as: "a" })
  );

export default function Card({ children, hover = false, as = "div", href, className = "", ...rest }: CardProps) {
  const cls = `card ${hover ? "card-hover" : ""} ${className} p-4`;
  if (as === "a") {
    const anchorProps = rest as AnchorHTMLAttributes<HTMLAnchorElement>;
    return (
      <a href={href} className={`${cls} block focus-ring`} {...anchorProps}>
        {children}
      </a>
    );
  }
  const divProps = rest as HTMLAttributes<HTMLDivElement>;
  return (
    <div className={cls} {...divProps}>
      {children}
    </div>
  );
}