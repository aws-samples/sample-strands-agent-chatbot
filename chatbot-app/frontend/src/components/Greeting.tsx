"use client"

import { useState } from "react"
import { Search, Zap, Wand2, FolderKanban, Code2, X } from "lucide-react"

type Prompt = { text: string; icon: string }

export const PROMPT_CATEGORIES = [
  {
    id: "search",
    label: "Search",
    icon: Search,
    prompts: [
      { text: "Search the web for the latest AI news and summarize key highlights", icon: "/tool-icons/duckduckgo.svg" },
      { text: "Find recent academic papers on large language models", icon: "/tool-icons/arxiv.svg" },
      { text: "Look up current stock price and recent news for Apple", icon: "/tool-icons/financial.svg" },
      { text: "What's the weather like in Seoul today?", icon: "/tool-icons/weather.png" },
      { text: "Find highly-rated Italian restaurants near Times Square in New York", icon: "/tool-icons/google-maps.svg" },
    ] as Prompt[],
  },
  {
    id: "automate",
    label: "Automate",
    icon: Zap,
    prompts: [
      { text: "Search for the best-selling wireless earbuds on Amazon under $50", icon: "/tool-icons/nova-act.png" },
      { text: "Find round-trip flights from Seoul to Tokyo next month under $300", icon: "/tool-icons/nova-act.png" },
      { text: "Compare prices for MacBook Pro 14-inch across major retailers", icon: "/tool-icons/nova-act.png" },
      { text: "Find available hotel rooms in Paris for this weekend", icon: "/tool-icons/nova-act.png" },
    ] as Prompt[],
  },
  {
    id: "create",
    label: "Create",
    icon: Wand2,
    prompts: [
      { text: "Create a PowerPoint presentation on the impact of AI in healthcare", icon: "/tool-icons/powerpoint.svg" },
      { text: "Build an Excel spreadsheet to track monthly expenses with charts", icon: "/tool-icons/excel.svg" },
      { text: "Draw an Excalidraw architecture diagram for a microservices app", icon: "/tool-icons/excalidraw.svg" },
      { text: "Generate a bar chart showing global EV sales by region in 2024", icon: "/tool-icons/visualization.svg" },
      { text: "Create a Word report summarizing Q4 business performance", icon: "/tool-icons/word.svg" },
    ] as Prompt[],
  },
  {
    id: "manage",
    label: "Manage",
    icon: FolderKanban,
    prompts: [
      { text: "Draft a professional email reply to a meeting request", icon: "/tool-icons/gmail.svg" },
      { text: "Create a calendar event for a team standup every Monday at 10am", icon: "/tool-icons/google-calendar.svg" },
      { text: "Summarize my Notion project page and list action items", icon: "/tool-icons/notion.svg" },
      { text: "List open pull requests in my GitHub repository", icon: "/tool-icons/github.svg" },
    ] as Prompt[],
  },
  {
    id: "code",
    label: "Code",
    icon: Code2,
    prompts: [
      { text: "Build a To-Do web app with React and Tailwind CSS", icon: "/tool-icons/code-agent.svg" },
      { text: "Analyze the code in my GitHub repo and write unit tests", icon: "/tool-icons/github.svg" },
      { text: "Design a landing page for a SaaS product with modern UI", icon: "/tool-icons/code-agent.svg" },
      { text: "Create a REST API server with FastAPI and PostgreSQL", icon: "/tool-icons/code-interpreter.svg" },
    ] as Prompt[],
  },
]

export function Greeting() {
  return (
    <div className="w-full flex flex-col justify-center items-center animate-fade-in">
      <h1 className="text-[28px] md:text-[34px] leading-tight font-semibold text-center text-foreground">
        <span>
          What are we working on?
        </span>
      </h1>
    </div>
  )
}

interface PromptSuggestionsProps {
  onSelectPrompt?: (prompt: string) => void
}

export function PromptSuggestions({ onSelectPrompt }: PromptSuggestionsProps) {
  const [activeCategory, setActiveCategory] = useState<string | null>(null)

  const active = PROMPT_CATEGORIES.find((c) => c.id === activeCategory)

  const handleCategoryClick = (id: string) => {
    setActiveCategory((prev) => (prev === id ? null : id))
  }

  const handlePromptClick = (prompt: string) => {
    onSelectPrompt?.(prompt)
    setActiveCategory(null)
  }

  return (
    <div className="w-full flex flex-col items-center gap-3">
      {/* Task categories */}
      <div className="flex flex-wrap justify-center gap-2">
        {PROMPT_CATEGORIES.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => handleCategoryClick(id)}
            className={`flex items-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium border transition-colors
              ${activeCategory === id
                ? "bg-accent text-accent-foreground border-primary/20"
                : "bg-card text-muted-foreground border-border hover:bg-muted hover:text-foreground"
              }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Example prompts panel — expands downward */}
      {active && (
        <div className="w-full max-w-xl rounded-lg border border-border bg-card shadow-xs animate-fade-in overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
              <active.icon className="w-4 h-4" />
              {active.label}
            </div>
            <button
              onClick={() => setActiveCategory(null)}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <ul>
            {active.prompts.map((prompt, i) => (
              <li key={i}>
                <button
                  onClick={() => handlePromptClick(prompt.text)}
                  className={`w-full text-left px-4 py-3 text-sm hover:bg-muted transition-colors flex items-center gap-3
                    ${i < active.prompts.length - 1 ? "border-b border-border" : ""}
                  `}
                >
                  <img src={prompt.icon} alt="" className="w-4 h-4 shrink-0 object-contain" />
                  {prompt.text}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
