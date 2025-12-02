import { useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage3.css';

export default function Stage3({ finalResponse, isSkipped }) {
  const contentRef = useRef(null);

  if (!finalResponse) {
    return null;
  }

  const handleExportPDF = async () => {
    // Dynamically import html2pdf.js
    const html2pdf = (await import('html2pdf.js')).default;
    
    if (!contentRef.current) return;

    const element = contentRef.current;
    const modelName = finalResponse.model.split('/')[1] || finalResponse.model;
    const filename = `council-response-${modelName}-${Date.now()}.pdf`;

    const opt = {
      margin: [0.5, 0.5, 0.5, 0.5],
      filename,
      image: { type: 'jpeg', quality: 0.98 },
      html2canvas: { scale: 2, useCORS: true },
      jsPDF: { unit: 'in', format: 'letter', orientation: 'portrait' },
    };

    html2pdf().set(opt).from(element).save();
  };

  return (
    <div className="stage stage3">
      {!isSkipped && <h3 className="stage-title">Stage 3: Final Council Answer</h3>}
      <div className="final-response">
        <button
          className="export-pdf-button"
          onClick={handleExportPDF}
          title="Export to PDF"
          aria-label="Export to PDF"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="12" y1="18" x2="12" y2="12" />
            <line x1="9" y1="15" x2="12" y2="18" />
            <line x1="15" y1="15" x2="12" y2="18" />
          </svg>
        </button>
        <div ref={contentRef} className="pdf-export-content">
          <div className="chairman-label">
            Chairman: {finalResponse.model.split('/')[1] || finalResponse.model}
          </div>
          <div className="final-text markdown-content">
            <ReactMarkdown>{finalResponse.response}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
