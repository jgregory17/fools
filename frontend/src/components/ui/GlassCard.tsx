"use client"

import { motion, HTMLMotionProps } from "framer-motion"
import { cn } from "@/lib/utils"

interface GlassCardProps extends HTMLMotionProps<"div"> {
  children: React.ReactNode
  className?: string
  hover?: boolean
  variant?: "elevated" | "active"
}

export function GlassCard({
  children,
  className,
  hover = false,
  variant = "elevated",
  ...props
}: GlassCardProps) {
  return (
    <motion.div
      className={cn(
        "relative overflow-hidden rounded-xl",
        variant === "elevated" && "bg-[#222222] border border-[#2d2d2d]",
        variant === "active" && "bg-[#2d2d2d] border border-[#ff6b35]",
        hover && "transition-standard hover:border-[#ff6b35]",
        className
      )}
      whileHover={hover ? { borderColor: "#ff6b35" } : undefined}
      {...props}
    >
      {/* Content */}
      <div className="relative z-10">
        {children}
      </div>
    </motion.div>
  )
}
