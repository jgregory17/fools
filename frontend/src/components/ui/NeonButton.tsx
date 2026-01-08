"use client"

import { motion, HTMLMotionProps } from "framer-motion"
import { cn } from "@/lib/utils"

interface NeonButtonProps extends HTMLMotionProps<"button"> {
  children: React.ReactNode
  variant?: "primary" | "secondary" | "ghost" | "danger"
  size?: "sm" | "md" | "lg"
  className?: string
}

const variants = {
  primary: {
    base: "bg-[#ff6b35] text-[#161616] border-none hover:brightness-110",
    text: "font-semibold",
  },
  secondary: {
    base: "bg-transparent text-[#f3f4f6] border border-[#2d2d2d] hover:border-[#ff6b35]",
    text: "font-medium",
  },
  ghost: {
    base: "bg-transparent text-[#94a3b8] border-none hover:text-[#f3f4f6] active:text-[#ff6b35]",
    text: "font-medium",
  },
  danger: {
    base: "bg-[#c53030] text-[#f3f4f6] border-none hover:brightness-110",
    text: "font-semibold",
  },
}

const sizes = {
  sm: "px-4 py-2 text-sm",
  md: "px-6 py-3 text-base",
  lg: "px-8 py-4 text-base",
}

export function NeonButton({
  children,
  variant = "primary",
  size = "md",
  className,
  disabled,
  ...props
}: NeonButtonProps) {
  const v = variants[variant]
  const s = sizes[size]

  return (
    <motion.button
      className={cn(
        "relative inline-flex items-center justify-center gap-2",
        "rounded-lg",
        "transition-fast",
        v.base,
        v.text,
        s,
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className
      )}
      whileTap={!disabled ? { scale: 0.98 } : undefined}
      disabled={disabled}
      {...props}
    >
      {children}
    </motion.button>
  )
}
