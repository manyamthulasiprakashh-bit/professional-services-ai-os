import { describe, expect, it } from 'vitest'
import { LOCAL_API_BASE_URL, apiBaseUrl, resolveApiBaseUrl } from './config'

describe('resolveApiBaseUrl', () => {
  it('falls back to the loopback backend in local development', () => {
    expect(resolveApiBaseUrl(undefined, { production: false })).toBe(
      LOCAL_API_BASE_URL,
    )
    expect(resolveApiBaseUrl('   ', { production: false })).toBe(
      LOCAL_API_BASE_URL,
    )
  })

  it('trims whitespace and trailing slashes', () => {
    expect(
      resolveApiBaseUrl('  https://api.test///  ', { production: true }),
    ).toBe('https://api.test')
  })

  it('accepts a same-origin path in production', () => {
    expect(resolveApiBaseUrl('/api', { production: true })).toBe('/api')
  })

  it.each([undefined, '', '   '])(
    'throws instead of using loopback in production when the value is %o',
    (value) => {
      expect(() => resolveApiBaseUrl(value, { production: true })).toThrow(
        /VITE_API_BASE_URL is required for production builds/,
      )
    },
  )

  it('never silently returns loopback for a production build', () => {
    let result: string | undefined
    try {
      result = resolveApiBaseUrl(undefined, { production: true })
    } catch {
      result = undefined
    }
    expect(result).not.toBe(LOCAL_API_BASE_URL)
  })

  it('exports the development default in the test environment', () => {
    expect(apiBaseUrl).toBe(LOCAL_API_BASE_URL)
  })
})
