/**
 * API Client for Python Backend
 * Base configuration and HTTP methods
 */

const env = (import.meta as any).env || {};
const API_BASE_URL = env.VITE_API_URL || 'http://localhost:8000/api';

class ApiClient {
  private baseURL: string;
  private token: string | null = null;

  constructor(baseURL: string) {
    this.baseURL = baseURL;
    this.loadToken();
  }

  private loadToken() {
    this.token = localStorage.getItem('auth_token');
  }

  setToken(token: string) {
    this.token = token;
    localStorage.setItem('auth_token', token);
  }

  clearToken() {
    this.token = null;
    localStorage.removeItem('auth_token');
  }

  private getHeaders(): HeadersInit {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };

    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }

    return headers;
  }

  async request<T>(
    endpoint: string,
    method: string = 'GET',
    data?: any
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;
    
    const options: RequestInit = {
      method,
      headers: this.getHeaders(),
    };

    if (data && (method === 'POST' || method === 'PUT' || method === 'PATCH')) {
      options.body = JSON.stringify(data);
    }

    try {
      const response = await fetch(url, options);

      if (!response.ok) {
        // Read the body once, before branching: the API puts the useful
        // explanation in `detail` (e.g. which role the account actually has).
        const errorData = await response.json().catch(() => ({} as any));
        const detail = typeof errorData?.detail === 'string' ? errorData.detail : '';

        if (response.status === 401) {
          this.clearToken();
          throw new Error(detail || 'Session expired. Please login again.');
        }
        if (response.status === 403) {
          throw new Error(detail || 'Access denied. Insufficient permissions.');
        }
        if (response.status === 404) {
          throw new Error(detail || 'Resource not found.');
        }
        throw new Error(detail || `HTTP Error: ${response.status}`);
      }

      // Handle 204 No Content
      if (response.status === 204) {
        return null as T;
      }

      return await response.json();
    } catch (error) {
      if (error instanceof Error) {
        throw error;
      }
      throw new Error('Network error. Please check your connection.');
    }
  }

  // Convenience methods
  get<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, 'GET');
  }

  post<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, 'POST', data);
  }

  put<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, 'PUT', data);
  }

  patch<T>(endpoint: string, data: any): Promise<T> {
    return this.request<T>(endpoint, 'PATCH', data);
  }

  delete<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, 'DELETE');
  }
}

// Export singleton instance
export const apiClient = new ApiClient(API_BASE_URL);

// Export for testing/debugging
export { API_BASE_URL };
