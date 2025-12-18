"use client"

import { motion, HTMLMotionProps } from "framer-motion"
import { cn } from "@/lib/utils"

interface NeonButtonProps extends HTMLMotionProps<"button"> {
  children: React.ReactNode
  variant?: "primary" | "secondary" | "danger"
  size?: "sm" | "md" | "lg"
  className?: string
}

const variants = {
  primary: {
    base: "from-cyan-500 to-blue-600",
    glow: "hover:shadow-cyan-500/50",
    text: "text-white",
  },
  secondary: {
    base: "from-purple-500 to-pink-600",
    glow: "hover:shadow-purple-500/50",
    text: "text-white",
  },
  danger: {
    base: "from-red-500 to-orange-600",
    glow: "hover:shadow-red-500/50",
    text: "text-white",
  },
}

const sizes = {
  sm: "px-4 py-2 text-sm gap-1.5",
  md: "px-6 py-3 text-base gap-2",
  lg: "px-8 py-4 text-lg gap-3",
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
        "relative inline-flex items-center justify-center font-semibold",
        "rounded-xl overflow-hidden",
        "bg-gradient-to-r",
        v.base,
        v.text,
        s,
        "transition-all duration-300",
        "hover:shadow-lg",
        v.glow,
        "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:shadow-none",
        className
      )}
      whileHover={!disabled ? { scale: 1.02 } : undefined}
      whileTap={!disabled ? { scale: 0.98 } : undefined}
      disabled={disabled}
      {...props}
    >
      {/* Shine effect */}
      <motion.div
        className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent"
        initial={{ x: "-100%" }}
        whileHover={{ x: "100%" }}
        transition={{ duration: 0.5 }}
      />

      {/* Content */}
      <span className="relative z-10 flex items-center gap-2">
        {children}
      </span>
    </motion.button>
  )
}
