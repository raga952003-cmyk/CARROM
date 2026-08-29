import React from 'react';
import { AlertOctagon, RotateCcw } from 'lucide-react';

/**
 * Last line of defence against the blank page.
 *
 * A crash while rendering unmounts the whole React tree, and what the operator
 * sees is white nothing with an explanation buried in the console. That has
 * happened here before, most memorably when a component read `setBoards` from a
 * closure before it was initialised and starting a match blanked the screen.
 *
 * This turns that into something a person mid-tournament can act on: what
 * broke, and a way back.
 */

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // The component stack is the part that actually locates the fault, and React
    // only hands it over here.
    console.error('[render crash]', error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="min-h-screen bg-[#F8F6F0] flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full overflow-hidden">
          <div className="px-5 py-4 bg-[#0B5D3B] text-white flex items-center gap-3">
            <AlertOctagon className="w-5 h-5 text-[#D4A72C]" />
            <h1 className="font-serif font-bold">This screen stopped responding</h1>
          </div>
          <div className="p-5 space-y-4">
            <p className="text-xs text-gray-700 leading-relaxed">
              Something went wrong while drawing this page. Your tournament data is
              safe — this happened in the browser, not on the server. Nothing you
              had already saved has been lost.
            </p>
            <div className="p-3 rounded-xl bg-gray-50 border border-gray-200">
              <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1">
                Details
              </div>
              <code className="text-[11px] text-red-800 break-words">
                {error.message || String(error)}
              </code>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={this.reset}
                className="px-4 py-2 text-xs font-bold bg-[#0B5D3B] hover:bg-[#08472d] text-white rounded-xl shadow-md flex items-center gap-1.5"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Try again</span>
              </button>
              <button
                onClick={() => window.location.reload()}
                className="px-4 py-2 text-xs font-bold text-gray-700 hover:bg-gray-100 rounded-xl border border-gray-200"
              >
                Reload the page
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
