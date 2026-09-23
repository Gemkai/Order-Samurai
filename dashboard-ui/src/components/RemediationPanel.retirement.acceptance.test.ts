import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { RemediationPanel } from './RemediationPanel'
import type { WIDPayload } from '@/types'
type Efficacy = NonNullable<WIDPayload['remediation_efficacy']>
it('renders retired repair evidence as collapsed history',()=>{const eff={attempted:8,completed:5,applied:2,improved:1,regressed:1,flat:0,success_rate:50,improvement_rate:12.5,by_skill:{wiki:{attempted:8,applied:2,improved:1}},events:[{metric:'Doc_Parity_Issues',skill:'wiki',command:'/wiki',before:7,after:3,outcome:'improved',used_at:'2026-09-12T12:00:00Z',actor:'ronin' as const}],note:'Historical data retained.'} as Efficacy;const html=renderToStaticMarkup(createElement(RemediationPanel,{eff}));expect(html).toContain('<details');expect(html).not.toMatch(/<details[^>]*\sopen(?:=|>)/);expect(html).toContain('Repair history');expect(html.toLowerCase()).toContain('autonomous repair is retired');expect(html).not.toContain('Remediation Efficacy');expect(html).not.toContain('Improvement rate');expect(html).toContain('Doc–Code Drift');expect(html).toContain('/wiki');expect(html).toContain('7');expect(html).toContain('3')})
