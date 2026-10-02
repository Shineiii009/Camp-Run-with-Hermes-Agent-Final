/**
 * LAYER 3 — DESKTOP PLUGIN (the face)
 * Suki Branch Triage panel for Hermes Desktop.
 *
 * Install (folder name MUST equal the `id` below):
 *   Windows: %LOCALAPPDATA%\hermes\desktop-plugins\suki-triage\plugin.js
 *   macOS/Linux: ~/.hermes/desktop-plugins/suki-triage/plugin.js
 * Then in Hermes Desktop: Ctrl/Cmd+K → "Reload desktop plugins", and enable it
 * under Capabilities → Plugins if needed. The app hot-reloads every save.
 *
 * Rules of the disk-plugin loader:
 *   * Only these imports resolve: '@hermes/plugin-sdk', 'react', 'react/jsx-runtime'.
 *   * The file is NOT compiled — use jsx()/jsxs() calls, not <JSX/> syntax.
 *   * No hardcoded colors — theme variables only.
 *   * Every identifier used in jsx() must be imported.
 * Docs: https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk
 *
 * Flow: button → prompt into the chat → Hermes loads suki-branch-triage →
 * MCP tools on data/store.db → ::suki-triage directive renders the board below.
 */

import { Badge, Button, cn, host, PALETTE_AREA, Tip, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const PLUGIN_ID = 'suki-triage' // MUST match the folder name

const BRANCHES = [
  'ALB', 'ORT', 'KAT', 'ERM', 'BGC', 'MKT',
  'CUB', 'PQE', 'KPT', 'TMR', 'MAN', 'MKN'
]

// Verdict → visual weight. Kept as theme tokens so it re-themes with the app.
// Tailwind's arbitrary-value syntax (text-(--token)) cannot carry a var()
// fallback, so these are single real tokens: `destructive` for an active
// stockout crisis, then progressively quieter as the verdict softens.
function verdictTone(verdict) {
  if (verdict === 'stockout-driven') return 'text-destructive'
  if (verdict === 'complaints-without-stockouts') return 'text-(--ui-text-secondary)'
  return 'text-(--ui-text-tertiary)'
}

function send(prompt) {
  const ok = host.composer.submit(null, prompt)
  if (!ok) {
    host.notify({ kind: 'info', message: 'Open or focus a chat first, then click again.' })
  }
}

// ---------------------------------------------------------------------------
// Pane: pick a branch (or the whole chain), then run one of the workflows.
// ---------------------------------------------------------------------------
function TriagePane() {
  const busy = useValue(host.state.busy) // true while the focused chat is working
  const [branch, setBranch] = useState('ALB')

  const actions = [
    {
      label: 'Daily triage brief',
      hint: 'Ranks all 12 branches by customer pain',
      prompt: 'Use the suki-branch-triage skill to give me the daily triage brief for the whole Suki Mart chain.'
    },
    {
      label: `Diagnose ${branch}`,
      hint: 'Stockouts → tickets → angry reviews, with evidence',
      prompt: `Use the suki-branch-triage skill to diagnose the ${branch} branch: what is out of stock, what complaints it caused, and what to restock.`
    },
    {
      label: `Restock plan for ${branch}`,
      hint: 'Transfer vs. supplier order, per SKU, with quantities',
      prompt: `Use the suki-branch-triage skill to build the restock plan for the ${branch} branch. For each item say whether to transfer from another branch or order from the supplier, and how many units.`
    },
    {
      label: 'Propose this week\'s transfers',
      hint: 'Drafts the moves — asks before writing anything',
      prompt: 'Use the suki-branch-triage skill to draft this week\'s branch-to-branch transfers for the stockout-driven branches. Propose them for my approval before writing anything.'
    }
  ]

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
    children: [
      jsxs('div', {
        children: [
          jsx('div', { className: 'font-medium', children: 'Suki Mart · Branch Triage' }),
          jsx('div', {
            className: 'text-xs text-(--ui-text-tertiary)',
            children: busy
              ? 'Hermes is working… results render in the chat.'
              : 'Stockouts are causing the complaints. Pick a branch, run a workflow.'
          })
        ]
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', {
            className: 'text-xs text-(--ui-text-tertiary)',
            children: 'Branch'
          }),
          jsx('div', {
            className: 'grid grid-cols-4 gap-1',
            children: BRANCHES.map(code =>
              jsx(Button, {
                key: code,
                variant: code === branch ? 'default' : 'ghost',
                size: 'sm',
                className: cn(
                  'justify-center px-1 text-xs',
                  code === branch && 'font-medium'
                ),
                onClick: () => setBranch(code),
                children: code
              })
            )
          })
        ]
      }),

      jsx('div', { className: 'h-px bg-(--ui-stroke-secondary)' }),

      ...actions.map(a =>
        jsxs('div', {
          key: a.label,
          className: 'flex flex-col gap-1 rounded-md border border-(--ui-stroke-secondary) p-2',
          children: [
            jsx(Button, {
              disabled: busy,
              className: 'justify-start',
              onClick: () => send(a.prompt),
              children: a.label
            }),
            jsx('div', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: a.hint
            })
          ]
        })
      )
    ]
  })
}

// ---------------------------------------------------------------------------
// Transcript directive: the agent emits ::suki-triage{...} to render the board
// inline in its own reply. Attrs are UNTRUSTED strings — parse defensively.
// ---------------------------------------------------------------------------
function num(value, fallback) {
  const n = parseFloat(value)
  return Number.isFinite(n) ? n : fallback
}

function TriageBoard({ attrs }) {
  attrs = attrs || {}
  const rows = String(attrs.rows || '')
    .split('|')
    .map(r => r.trim())
    .filter(Boolean)

  if (!rows.length) {
    return jsx('div', {
      className: 'rounded-md border border-(--ui-stroke-secondary) p-2 text-xs text-(--ui-text-tertiary)',
      children: 'No branches to show.'
    })
  }

  return jsxs('div', {
    className: 'flex flex-col gap-1.5 rounded-md border border-(--ui-stroke-secondary) p-2 text-xs',
    children: [
      jsx('div', {
        className: 'font-medium',
        children: String(attrs.headline || 'Suki Mart · triage')
      }),
      ...rows.map((row, idx) => {
        // code,stockouts,open,unanswered,rating,verdict — attrs are untrusted,
        // so tolerate missing fields instead of throwing on destructuring.
        const f = row.split(',')
        const code = (f[0] || '?').trim()
        const stockouts = f[1]
        const open = f[2]
        const unanswered = f[3]
        const rating = f[4]
        const verdict = (f[5] || '').trim()
        return jsxs('div', {
          key: `${code}-${idx}`,
          className: 'flex items-center gap-2',
          children: [
            jsx('span', { className: 'w-9 shrink-0 font-medium', children: code }),
            jsxs('span', {
              className: cn('w-14 shrink-0', verdictTone((verdict || '').trim())),
              children: `${num(stockouts, 0)} out`
            }),
            jsx('span', {
              className: 'w-16 shrink-0 text-(--ui-text-secondary)',
              children: `${num(open, 0)} open`
            }),
            jsx('span', {
              className: 'w-20 shrink-0 text-(--ui-text-secondary)',
              children: `${num(unanswered, 0)} unanswered`
            }),
            jsx('span', {
              className: 'shrink-0 text-(--ui-text-tertiary)',
              children: `★${num(rating, 0).toFixed(1)}`
            }),
            jsx(Badge, {
              className: cn('ml-auto', verdictTone((verdict || '').trim())),
              children: (verdict || '').trim() || '—'
            })
          ]
        })
      }),
      jsx('div', {
        className: 'text-(--ui-text-tertiary)',
        children: String(attrs.note || '')
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Statusbar chip: the network's worst branch at a glance.
// ---------------------------------------------------------------------------
function TriageChip() {
  return jsx(Tip, {
    label: 'Suki Mart — run the daily triage brief',
    children: jsx('button', {
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      type: 'button',
      onClick: () =>
        send('Use the suki-branch-triage skill to give me the daily triage brief for the whole Suki Mart chain.'),
      children: 'suki triage'
    })
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Suki Branch Triage',
  register(ctx) {
    // A pane docked on the right side of the window.
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Suki Triage',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(TriagePane, {})
    })

    // Lets the assistant render the triage board inside its own reply.
    ctx.register({
      id: 'triage-board',
      area: 'transcript.directives',
      data: {
        name: 'suki-triage',
        render: ({ attrs }) => jsx(TriageBoard, { attrs })
      }
    })

    // A statusbar chip.
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 130,
      render: () => jsx(TriageChip, {})
    })

    // ⌘K / Ctrl+K commands.
    const commands = [
      {
        id: 'brief',
        label: 'Suki: Daily triage brief',
        prompt: 'Use the suki-branch-triage skill to give me the daily triage brief for the whole Suki Mart chain.'
      },
      {
        id: 'transfers',
        label: 'Suki: Draft this week\'s stock transfers',
        prompt: 'Use the suki-branch-triage skill to draft this week\'s branch-to-branch transfers for the stockout-driven branches. Propose them for my approval before writing anything.'
      }
    ]

    commands.forEach((cmd, i) => {
      ctx.register({
        id: cmd.id,
        area: PALETTE_AREA,
        order: 100 + i,
        data: {
          id: `${PLUGIN_ID}.${cmd.id}`,
          label: cmd.label,
          keywords: ['suki', 'camp', 'run', 'triage', 'stock', 'branch', 'restock'],
          run: () => send(cmd.prompt)
        }
      })
    })
  }
}