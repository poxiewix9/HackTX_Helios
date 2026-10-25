import { cva } from "class-variance-authority";
import type { VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider",
  {
    variants: {
      tone: {
        neutral: "border-ops-edge bg-ops-panel text-ops-dim",
        ok: "border-ops-ok/60 bg-ops-ok/10 text-ops-ok",
        warn: "border-ops-warn/60 bg-ops-warn/10 text-ops-warn",
        danger: "border-ops-danger/60 bg-ops-danger/10 text-ops-danger",
        accent: "border-ops-accent/60 bg-ops-accent/10 text-ops-accent",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
