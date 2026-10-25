import { cva } from "class-variance-authority";
import type { VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border text-xs font-medium transition-colors disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ops-accent",
  {
    variants: {
      variant: {
        default:
          "border-ops-edge bg-ops-panel text-ops-text hover:border-ops-accent hover:text-ops-accent",
        accent: "border-ops-accent bg-ops-accent/15 text-ops-accent hover:bg-ops-accent/25",
        danger: "border-ops-danger bg-ops-danger/15 text-ops-danger hover:bg-ops-danger/25",
        ghost: "border-transparent text-ops-dim hover:text-ops-text",
      },
      size: {
        sm: "h-7 px-2",
        md: "h-8 px-3",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return (
    <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}
