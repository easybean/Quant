import { Moon, Sun } from 'lucide-react'

type ThemeToggleProps = {
  theme: 'light' | 'dark'
  onToggle: () => void
}

export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  return (
    <button className="icon-button" type="button" onClick={onToggle} aria-label={theme === 'light' ? '切换深色主题' : '切换浅色主题'}>
      {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
    </button>
  )
}
