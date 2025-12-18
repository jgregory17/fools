"use client"

import { motion, HTMLMotionProps } from "framer-motion"
import { cn } from "@/lib/utils"

interface GlassCardProps extends HTMLMotionProps<"div"> {
  children: React.ReactNode
  className?: string
  hover?: boolean
}

export function GlassCard({ children, className, hover = true, ...props }: GlassCardProps) {
  return (
    <motion.div
      className={cn(
        "relative overflow-hidden",
        "bg-white/5 backdrop-blur-xl",
        "border border-white/10",
        "rounded-2xl",
        "shadow-2xl shadow-black/20",
        hover && "transition-all duration-300 hover:bg-white/[0.07] hover:border-white/20",
        className
      )}
      whileHover={hover ? { scale: 1.01 } : undefined}
      {...props}
    >
      {/* Gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-br from-white/[0.02] to-transparent pointer-events-none" />

      {/* Content */}
      <div className="relative z-10">
        {children}
      </div>
    </motion.div>
  )
}
